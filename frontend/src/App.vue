<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue"
import { api, type StreamEvent } from "./api"
import ReportCard from "./components/ReportCard.vue"
import ResultTableCard from "./components/ResultTableCard.vue"
import MarkdownReport from "./components/MarkdownReport.vue"
import type { AuthUser, ConversationSummary, QueryResult, SavedMemory, SchemaField, SchemaTable, WorkspaceConfig, ReportDataSource } from "./types"

interface Turn { id: string; query: string; result?: QueryResult; fields: Record<string,string>; table?: {columns:string[]; rows:QueryResult['rows']; sql:string}; progress: string; pending: boolean; error?: string; resolved?: boolean; createdAt: string; resultAt?: string; historical?: boolean; canResume?: boolean }
interface Conversation extends ConversationSummary { turns: Turn[]; draft: string; scroll: number; loaded: boolean }
const authReady = ref(false), authUser = ref<AuthUser|null>(null), loginUsername = ref(""), loginPassword = ref(""), showPassword = ref(false), loginLoading = ref(false)
const schema = ref<SchemaTable[]>([]), memories = ref<SavedMemory[]>([]), conversations = ref<Conversation[]>([]), activeId = ref("")
const view = ref("chat"), input = ref(""), loading = ref(false), error = ref(""), toast = ref("")
const scroll = ref<HTMLElement|null>(null), mobileSidebar = ref(false), mobileContext = ref(false)
const workspace = ref<WorkspaceConfig>({schema_fields:[], analysis_table_ids:[]}), expandedSql = ref<string[]>([])
const search = ref(""), database = ref(""), currentTableId = ref(""), selectedOnly = ref(false), descriptions = ref(true), contextSearch = ref("")
const selectionUndo = ref<WorkspaceConfig['schema_fields']>(), busyAssets = ref(false)
const retries = new Map<string, () => void>()
const historyLoading = ref(false)
const workspaceWrites = new Map<string, Promise<unknown>>()
const active = computed(() => conversations.value.find(c => c.id === activeId.value))
const selected = computed(() => workspace.value.schema_fields ?? [])
const savedKeys = computed(() => new Set(memories.value.filter(m => m.kind==='schema_field').map(m => `${m.table_id}:${m.name}`)))
const tables = computed(() => memories.value.filter(m => m.kind==='result_table'))
const referenceIds = computed(() => workspace.value.analysis_table_ids ?? [])
const references = computed(() => tables.value.filter(m => referenceIds.value.includes(m.task_id!)))
const databases = computed(() => [...new Set(schema.value.map(t => t.database || 'askdata_mock'))])
const filteredTables = computed(() => schema.value.filter(t => (!database.value || t.database===database.value) && (!search.value || `${t.label} ${t.id} ${t.description} ${t.fields.map(f=>`${f.name} ${f.label} ${f.description??''}`).join(' ')}`.toLowerCase().includes(search.value.toLowerCase()))))
const currentTable = computed(() => filteredTables.value.find(t=>t.id===currentTableId.value) ?? filteredTables.value[0])
const visibleFields = computed(() => (currentTable.value?.fields ?? []).filter(f => (!selectedOnly.value || hasField(currentTable.value!.id, f.name)) && (!search.value || `${currentTable.value?.label} ${currentTable.value?.id} ${f.name} ${f.label} ${f.description??''}`.toLowerCase().includes(search.value.toLowerCase()))))
const contextFields = computed(() => selected.value.filter(f => `${fieldLabel(f.tableId!, f.name)} ${f.name} ${f.tableId}`.toLowerCase().includes(contextSearch.value.toLowerCase())))
const mixedDatabases = computed(() => new Set(selected.value.map(f=>schema.value.find(t=>t.id===f.tableId)?.database)).size > 1)
const reportSources = computed<ReportDataSource[]>(() => {
  const sources = new Map<string,ReportDataSource>()
  for(const c of conversations.value) for(const turn of c.turns) if(turn.result?.rows.length) sources.set(turn.result.task_id,{taskId:turn.result.task_id,title:turn.result.result_title||'查询结果',columns:turn.result.columns,rows:turn.result.rows})
  for(const c of conversations.value) for(const table of c.workspace.analysis_tables||[]) if(!sources.has(table.task_id))sources.set(table.task_id,{taskId:table.task_id,title:table.title,columns:table.columns,rows:table.rows})
  for(const m of tables.value) if(m.task_id) sources.set(m.task_id,{taskId:m.task_id,title:m.title||'查询结果',columns:m.columns||[],rows:m.rows||[]})
  return [...sources.values()]
})
const streamingText = (turn:Turn) => turn.fields.markdown || turn.fields.answer || turn.fields.response || turn.fields.analysis || ''
const title = computed(() => view.value==='catalog' ? '数据目录' : view.value==='assets' ? '已存资产' : active.value?.title || '新对话')
function notify(message:string) {toast.value=message;window.setTimeout(()=>toast.value='',3000)}
function fieldLabel(tableId:string,name:string) {return schema.value.find(t=>t.id===tableId)?.fields.find(f=>f.name===name)?.label || name}
function tableLabel(tableId:string) {return schema.value.find(t=>t.id===tableId)?.label || tableId}
function hasField(tableId:string,name:string) {return selected.value.some(f=>f.tableId===tableId&&f.name===name)}
function cloneWorkspace(value:WorkspaceConfig):WorkspaceConfig {return JSON.parse(JSON.stringify(value))}
function rememberWorkspace() {
  const c=active.value;if(!c)return
  c.workspace=cloneWorkspace(workspace.value)
  const snapshot=cloneWorkspace(c.workspace)
  const write=(workspaceWrites.get(c.id)??Promise.resolve()).catch(()=>{}).then(()=>api.updateConversation(c.id,snapshot))
  workspaceWrites.set(c.id,write)
  void write.catch(e=>{error.value=e instanceof Error?`查询上下文保存失败：${e.message}`:'查询上下文保存失败'}).finally(()=>{if(workspaceWrites.get(c.id)===write)workspaceWrites.delete(c.id)})
}
async function flushWorkspace() {await Promise.all([...workspaceWrites.values()])}
function activeStorageKey() {return `askdata_active_conversation:${authUser.value?.user_id}`}
function rememberActive() {try{localStorage.setItem(activeStorageKey(),activeId.value)}catch{}}
function previousActive() {try{return localStorage.getItem(activeStorageKey())}catch{return null}}
function formatTime(value?:string) {return value?new Date(value).toLocaleString('zh-CN',{hour12:false}):''}
function tableTime(turn:Turn) {return `${turn.historical?'历史快照 · ':'生成于 '}${formatTime(turn.resultAt||turn.createdAt)}`}
function originalQuestion(turn:Turn) {return active.value?.turns.find(t=>t.result?.task_id===turn.result?.task_id)?.query||turn.query}
function changeFields(fields:NonNullable<WorkspaceConfig['schema_fields']>) {selectionUndo.value=[...selected.value];workspace.value.schema_fields=fields;rememberWorkspace()}
function toggleField(tableId:string,f:SchemaField) {changeFields(hasField(tableId,f.name)?selected.value.filter(x=>x.tableId!==tableId||x.name!==f.name):[...selected.value,{tableId,name:f.name,aggregation:'auto'}])}
function removeField(tableId:string,name:string) {changeFields(selected.value.filter(f=>f.tableId!==tableId||f.name!==name))}
function selectAll() {const t=currentTable.value;if(!t)return;changeFields([...selected.value,...visibleFields.value.filter(f=>!hasField(t.id,f.name)).map(f=>({tableId:t.id,name:f.name,aggregation:'auto' as const}))])}
function clearTable() {const id=currentTable.value?.id;changeFields(selected.value.filter(f=>f.tableId!==id))}
function undo() {if(selectionUndo.value){const previous=selectionUndo.value;selectionUndo.value=undefined;workspace.value.schema_fields=previous;rememberWorkspace()}}
function toggleReference(id:string) {workspace.value.analysis_table_ids=referenceIds.value.includes(id)?referenceIds.value.filter(x=>x!==id):[...referenceIds.value,id];rememberWorkspace()}
async function safe(operation:()=>Promise<unknown>) {try {await operation()} catch(e) {error.value=e instanceof Error?e.message:'操作失败'}}
async function favorite(tableId:string,f:SchemaField) {if(busyAssets.value)return;busyAssets.value=true;await safe(async()=>{if(savedKeys.value.has(`${tableId}:${f.name}`))await api.deleteMemory(`field:${tableId}.${f.name}`);else await api.saveField(tableId,f);memories.value=await api.memories()});busyAssets.value=false}
async function deleteMemory(m:SavedMemory) {if(!window.confirm(`删除“${m.title||m.label||m.name}”？`))return;await safe(async()=>{await api.deleteMemory(m.id);memories.value=await api.memories();notify('已删除')})}
async function saveResult(result:QueryResult) {await safe(async()=>{await api.save(result.task_id);result.saved=true;memories.value=await api.memories();notify('结果已保存')})}
function savePosition() {if(active.value&&view.value==='chat'){active.value.scroll=scroll.value?.scrollTop||0;active.value.draft=input.value}}
function changeView(next:string) {savePosition();view.value=next;mobileSidebar.value=false;if(next==='chat')nextTick(()=>{if(scroll.value)scroll.value.scrollTop=active.value?.scroll||0})}
function fromSummary(item:ConversationSummary):Conversation {return {...item,turns:[],draft:'',scroll:0,loaded:false}}
async function activateConversation(id:string) {
  const c=conversations.value.find(c=>c.id===id);if(!c)return
  if(!c.loaded){
    const detail=await api.conversation(id)
    Object.assign(c,{...detail,turns:detail.turns.map(t=>({id:t.id,query:t.query,result:t.result??undefined,fields:{},progress:'',pending:false,error:t.error??undefined,resolved:t.resolved,canResume:t.can_resume,createdAt:t.created_at,resultAt:t.result_at??undefined,historical:true})),loaded:true})
  }
  activeId.value=id;rememberActive();workspace.value=cloneWorkspace(c.workspace);input.value=c.draft
  selectionUndo.value=undefined;contextSearch.value='';view.value='chat';error.value='';mobileSidebar.value=false
  await nextTick();if(scroll.value)scroll.value.scrollTop=c.scroll
}
async function selectConversation(id:string) {
  if(historyLoading.value)return
  savePosition();historyLoading.value=true
  try{await flushWorkspace();await activateConversation(id)}catch(e){error.value=e instanceof Error?e.message:'无法加载对话'}finally{historyLoading.value=false}
}
async function createConversation() {
  if(loading.value||historyLoading.value)return
  savePosition();historyLoading.value=true
  try{await flushWorkspace();const item=await api.createConversation();conversations.value.unshift({...fromSummary(item),loaded:true});await activateConversation(item.id)}catch(e){error.value=e instanceof Error?e.message:'创建对话失败'}finally{historyLoading.value=false}
}
async function removeConversation(id:string) {
  if(loading.value||historyLoading.value)return
  const c=conversations.value.find(c=>c.id===id)
  if(!window.confirm(`删除“${c?.title||'此对话'}”及其聊天记录？已存资产会保留。`))return
  historyLoading.value=true
  try{
    await flushWorkspace();await api.deleteConversation(id)
    for(const turn of c?.turns||[])retries.delete(turn.id)
    conversations.value=conversations.value.filter(c=>c.id!==id)
    if(activeId.value===id){
      activeId.value='';workspace.value={};input.value=''
      if(!conversations.value.length){const item=await api.createConversation();conversations.value.push({...fromSummary(item),loaded:true})}
      await activateConversation(conversations.value[0]!.id)
    }
  }catch(e){error.value=e instanceof Error?e.message:'删除对话失败'}finally{historyLoading.value=false}
}
async function initialize() {
  schema.value=[];memories.value=[];conversations.value=[];activeId.value='';selectionUndo.value=undefined;expandedSql.value=[];contextSearch.value='';retries.clear()
  historyLoading.value=true
  try{
    const [s,m,list]=await Promise.all([api.schema(),api.memories(),api.conversations()])
    schema.value=s;memories.value=m;conversations.value=list.map(fromSummary)
    if(!list.length){const item=await api.createConversation();conversations.value.push({...fromSummary(item),loaded:true})}
    const last=previousActive();await activateConversation(conversations.value.some(c=>c.id===last)?last!:conversations.value[0]!.id)
    search.value='';database.value='';currentTableId.value=''
  }finally{historyLoading.value=false}
}
async function loginUser() {if(loginLoading.value)return;loginLoading.value=true;error.value='';await safe(async()=>{authUser.value=await api.login(loginUsername.value.trim(),loginPassword.value);loginPassword.value='';await initialize()});loginLoading.value=false}
async function logoutUser() {if(loading.value||historyLoading.value)return;await safe(async()=>{await flushWorkspace();await api.logout();authUser.value=null;activeId.value='';conversations.value=[];schema.value=[];memories.value=[];workspace.value={};input.value='';loginPassword.value='';error.value=''})}
function demoAccount(username:string) {loginUsername.value=username;loginPassword.value=`${username}123`}
onMounted(async()=>{if(api.hasSession()){try {authUser.value=await api.me();await initialize()}catch(e){api.clearSession();authUser.value=null;error.value=e instanceof Error?e.message:'初始化失败'}}authReady.value=true})
function requestWorkspace(source?:QueryResult):WorkspaceConfig {return {...workspace.value,analysis_table_ids:[...new Set([...referenceIds.value,...(source?.rows.length?[source.task_id]:[])])],analysis_tables:[...references.value.map(t=>({task_id:t.task_id!,title:t.title||'查询结果',query:t.query||'',columns:t.columns||[],rows:(t.rows||[]).slice(0,50)})),...(source?.rows.length&&!references.value.some(t=>t.task_id===source.task_id)?[{task_id:source.task_id,title:source.result_title||'查询结果',query:active.value?.turns.find(t=>t.result?.task_id===source.task_id)?.query||'',columns:source.columns,rows:source.rows.slice(0,50)}]:[])]}}
async function submit(text?:string, source?:QueryResult, clarification?:{taskId:string;optionId:string;turn:Turn}) {
  const query=(text??input.value).trim();if(!query||loading.value||historyLoading.value)return
  if(!active.value)await createConversation();if(!active.value)return
  const c=active.value;const payload=requestWorkspace(source)
  const turn:Turn={id:crypto.randomUUID(),query,fields:{},progress:'正在理解问题…',pending:true,createdAt:new Date().toISOString()}
  c.turns.push(turn);const target=c.turns[c.turns.length-1]!;if(c.title==='新对话')c.title=query.slice(0,22)
  loading.value=true;input.value='';c.draft='';error.value='';view.value='chat'
  retries.set(turn.id,()=>void submit(query,source,clarification))
  await nextTick();scroll.value?.scrollTo({top:scroll.value.scrollHeight})
  const receive=(event:StreamEvent)=>{
    const follow=activeId.value===c.id&&view.value==='chat'&&Boolean(scroll.value&&scroll.value.scrollHeight-scroll.value.scrollTop-scroll.value.clientHeight<100)
    if(event.event==='progress')target.progress=event.message||'正在处理…'
    if(event.event==='text'&&event.field)target.fields[event.field]=event.text||''
    if(event.event==='table')target.table={columns:event.columns||[],rows:event.rows||[],sql:event.sql||''}
    if(event.event==='result'&&event.result)target.result=event.result
    if(follow)nextTick(()=>scroll.value?.scrollTo({top:scroll.value.scrollHeight}))
  }
  try {
    await flushWorkspace()
    target.result=clarification?await api.clarifyStream(clarification.taskId,clarification.optionId,receive):await api.queryStream(query,payload,c.id,receive)
    target.resultAt=new Date().toISOString();target.canResume=target.result.status==='waiting_clarification';c.updated_at=target.resultAt
    if(clarification)clarification.turn.resolved=true
    // Typed clarification replies also consume the pending choice in the same task.
    for(const old of c.turns)if(old!==target&&old.result?.status==='waiting_clarification'){old.canResume=false;if(old.result.task_id===target.result.task_id)old.resolved=true}
  } catch(e) {target.error=e instanceof Error?e.message:'回复中断，请重试'}
  finally {
    target.pending=false;loading.value=false
    if(target.result){target.resultAt??=new Date().toISOString();target.canResume??=target.result.status==='waiting_clarification';target.fields={};target.table=undefined;retries.delete(turn.id)}
    conversations.value.sort((a,b)=>b.updated_at.localeCompare(a.updated_at))
  }
}
function onEnter(e:KeyboardEvent) {if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();void submit()}}
function toggleSql(id:string) {expandedSql.value=expandedSql.value.includes(id)?expandedSql.value.filter(x=>x!==id):[...expandedSql.value,id]}
function dragField(e:DragEvent,t:SchemaTable,f:SchemaField) {e.dataTransfer?.setData('application/askdata-field',JSON.stringify({tableId:t.id,name:f.name}))}
function dropField(e:DragEvent) {try{const data=JSON.parse(e.dataTransfer?.getData('application/askdata-field')||'null');const f=schema.value.find(t=>t.id===data?.tableId)?.fields.find(f=>f.name===data?.name);if(f&&!hasField(data.tableId,f.name))toggleField(data.tableId,f)}catch{}}
</script>

<template>
  <div v-if="!authReady" class="boot-state">正在连接 AskData…</div>
  <main v-else-if="!authUser" class="signin-page">
    <div class="signin-brand"><span class="signin-symbol">A</span>AskData Studio</div>
    <section class="signin-main"><h1>登录 AskData</h1><p class="signin-description">使用你的工作空间账号继续。</p>
      <form @submit.prevent="loginUser">
        <label class="auth-field" for="username">账号</label><div class="auth-input-wrap"><input id="username" v-model="loginUsername" autocomplete="username" placeholder="请输入账号" required></div>
        <label class="auth-field" for="password">密码</label><div class="auth-input-wrap"><input id="password" v-model="loginPassword" :type="showPassword?'text':'password'" autocomplete="current-password" placeholder="请输入密码" required><button type="button" class="auth-password-toggle" :aria-label="showPassword?'隐藏密码':'显示密码'" @click="showPassword=!showPassword">{{ showPassword?'隐藏':'显示' }}</button></div>
        <p v-if="error" class="auth-error" role="alert">{{ error }}</p><button class="auth-submit" :disabled="loginLoading">{{ loginLoading?'正在登录…':'登录' }}</button>
      </form>
      <details class="auth-demo"><summary>使用演示账号</summary><button v-for="account in [['admin','管理员','全部数据'],['sales','电商运营','电商数据'],['mock','基础演示','Mock 数据']]" :key="account[0]" type="button" class="auth-demo-option" @click="demoAccount(account[0]!)"><span class="account-letter">{{ account[1]?.slice(0,1) }}</span><span>{{ account[1] }}<small>{{ account[0] }} · {{ account[2] }}</small></span><span>↗</span></button></details>
    </section><footer class="signin-footer"><span>AskData Studio</span><span>数据查询与分析</span></footer>
  </main>
  <div v-else class="shell" :class="{'directory-mode':view==='catalog','chat-mode':view==='chat'}">
    <button v-if="mobileSidebar||mobileContext" class="mobile-scrim" aria-label="关闭侧栏" @click="mobileSidebar=false;mobileContext=false"></button>
    <aside class="sidebar" :class="{open:mobileSidebar}">
      <div class="brand"><span class="brand-mark">A</span><span>AskData<small>数据查询工作空间</small></span></div>
      <div class="workspace-switch"><span>{{ authUser.display_name.slice(0,1) }}</span>{{ authUser.display_name }}的工作空间</div>
      <button class="new-query" :disabled="loading||historyLoading" @click="createConversation">＋ 新建对话</button>
      <nav class="navigation" aria-label="主导航"><button class="nav-item" :class="{active:view==='chat'}" @click="changeView('chat')">◌ 数据查询</button><button class="nav-item" :class="{active:view==='catalog'}" @click="changeView('catalog')">▦ 数据目录<span class="nav-count">{{ schema.length }}</span></button><button class="nav-item" :class="{active:view==='assets'}" @click="changeView('assets')">▱ 已存资产<span class="nav-count">{{ memories.length }}</span></button></nav>
      <div class="history-head">对话记录</div><div class="history"><div v-for="c in conversations" :key="c.id" class="history-item" :class="{active:c.id===activeId&&view==='chat'}"><button class="history-select" :disabled="historyLoading" :title="`${c.title} · ${formatTime(c.updated_at)}`" @click="selectConversation(c.id)">{{ c.title }}</button><button class="history-remove" :disabled="loading||historyLoading" :aria-label="`删除${c.title}`" @click="removeConversation(c.id)">×</button></div></div>
      <div class="sidebar-bottom"><div class="user-menu"><span class="avatar">{{ authUser.display_name.slice(0,1) }}</span><span class="user-name">{{ authUser.display_name }}<small>{{ authUser.username }} · {{ schema.length }} 张数据表</small></span><button class="icon-button" :disabled="loading||historyLoading" title="退出登录" @click="logoutUser">↪</button></div></div>
    </aside>
    <main class="app-body">
      <header class="topbar"><div class="breadcrumb"><button class="mobile-nav icon-button" aria-label="打开导航" @click="mobileSidebar=true">☰</button><span>工作空间</span><span>/</span><b>{{ title }}</b></div><div class="top-actions"><span class="connection"><i></i>已连接</span><button class="icon-button" aria-label="切换查询上下文" @click="mobileContext=!mobileContext">☷</button></div></header>
      <div class="body-columns" :inert="historyLoading||undefined">
        <section class="work-column">
          <div v-show="view==='catalog'" class="directory-screen">
            <header class="directory-header"><div class="directory-heading"><h1>数据目录<span class="directory-count">{{ schema.length }} 张表</span></h1></div><p class="directory-subtitle">选择查询字段，或按表名、字段和业务含义搜索。</p><div class="directory-filters"><label class="directory-search"><span>⌕</span><input v-model="search" aria-label="搜索数据表或字段" placeholder="搜索表名、字段或业务含义"><button v-if="search" aria-label="清空搜索" @click="search=''">×</button></label><select v-model="database" aria-label="筛选数据库"><option value="">全部数据库</option><option v-for="db in databases" :key="db">{{ db }}</option></select></div></header>
            <div class="directory-split"><div class="directory-table-pane"><div class="directory-table-label">数据表 <b>{{ filteredTables.length }}</b></div><div class="directory-table-scroll"><button v-for="t in filteredTables" :key="t.id" class="directory-table-button" :class="{'is-active':currentTable?.id===t.id}" @click="currentTableId=t.id"><span class="table-label">{{ t.label }}<small>{{ t.database }} / {{ t.name||t.id }}</small></span><span class="selection-number">{{ selected.filter(f=>f.tableId===t.id).length||'' }}</span></button><p v-if="!filteredTables.length" class="directory-empty">没有匹配的数据表</p></div></div>
              <div class="directory-fields-pane"><template v-if="currentTable"><header class="directory-fields-header"><div class="directory-fields-title"><h2>{{ currentTable.label }}<code>{{ currentTable.database }} / {{ currentTable.name||currentTable.id }}</code></h2><span class="count">{{ currentTable.fields.length }} 个字段</span></div><p class="directory-fields-description">{{ currentTable.description }}</p><div class="directory-field-actions"><div><button class="btn" @click="selectAll">添加全部匹配字段</button><button class="btn" @click="clearTable">清除此表</button></div><label class="selected-only"><input v-model="selectedOnly" type="checkbox">只看已选</label><label class="selected-only"><input v-model="descriptions" type="checkbox">字段说明</label></div></header>
                <div class="directory-fields-scroll"><div v-for="f in visibleFields" :key="`${currentTable.id}:${f.name}`" class="directory-field-row" :class="{'is-selected':hasField(currentTable.id,f.name)}" draggable="true" @dragstart="dragField($event,currentTable,f)"><input :id="`field-${currentTable.id}-${f.name}`" type="checkbox" :checked="hasField(currentTable.id,f.name)" @change="toggleField(currentTable.id,f)"><label class="directory-field-label" :for="`field-${currentTable.id}-${f.name}`"><span class="directory-field-name">{{ f.label }}<span class="type-inline">{{ f.type }}</span></span><code>{{ f.name }}</code><p v-if="descriptions">{{ f.description }}</p></label><button class="field-favorite" :disabled="busyAssets" :class="{selected:savedKeys.has(`${currentTable.id}:${f.name}`)}" :aria-label="`收藏${f.label}`" @click="favorite(currentTable.id,f)">{{ savedKeys.has(`${currentTable.id}:${f.name}`)?'★':'☆' }}</button></div><p v-if="!visibleFields.length" class="directory-empty">没有匹配的字段</p></div>
              </template><div v-else class="directory-empty">请选择数据表</div><footer class="directory-footer"><div class="directory-footer-left">已选 <strong>{{ selected.length }}</strong> 个字段<button v-if="selectionUndo" @click="undo">撤销</button><button :disabled="!selected.length" @click="changeFields([])">清空</button></div><button class="btn primary" @click="changeView('chat')">返回查询 ↗</button></footer></div>
            </div>
          </div>
          <div v-show="view==='assets'" class="assets-screen"><h1>已存资产</h1><p class="doc-description">保存的结果可以作为分析依据；常用字段可以直接添加到查询。</p><h2>查询结果</h2><div v-for="m in tables" :key="m.id" class="asset-row"><div><strong>{{ m.title||'查询结果' }}</strong><small>{{ m.rows?.length||0 }} 行 · {{ m.query }}</small></div><button class="btn" @click="toggleReference(m.task_id!)">{{ referenceIds.includes(m.task_id!)?'移除参考':'添加为参考' }}</button><button class="icon-button" aria-label="删除资产" @click="deleteMemory(m)">×</button></div><p v-if="!tables.length" class="directory-empty">查询完成后，点击“保存结果”即可在这里查看。</p><h2>常用字段</h2><div v-for="m in memories.filter(m=>m.kind==='schema_field')" :key="m.id" class="asset-row"><div><strong>{{ m.label||m.name }}</strong><small>{{ tableLabel(m.table_id!) }} · {{ m.name }}</small></div><button class="btn" :disabled="hasField(m.table_id!,m.name!)" @click="changeFields([...selected,{tableId:m.table_id!,name:m.name!,aggregation:'auto'}])">添加到查询</button><button class="icon-button" aria-label="删除常用字段" @click="deleteMemory(m)">×</button></div></div>
          <div v-show="view==='chat'" ref="scroll" class="document-scroll" @scroll="view==='chat'&&active&&(active.scroll=($event.target as HTMLElement).scrollTop)">
            <div class="document">
              <p v-if="historyLoading" class="pending-copy" role="status">正在加载对话记录…</p>
              <div v-else-if="!active?.turns.length" class="empty-doc"><div class="empty-mark">▦</div><h1>从一个业务问题开始</h1><p>输入想了解的指标，或先浏览数据目录。<br>查询结果、解释与报告会保留在同一段对话里。</p><div class="quick-label">你可以这样开始</div><button class="prompt-row" @click="input='按地区统计销售额'">按地区统计销售额 <span>↗</span></button><button class="prompt-row" @click="changeView('catalog')">浏览数据表与字段 <span>↗</span></button></div>
              <template v-else><header class="conversation-heading"><div class="doc-meta">数据查询 / 对话</div><h1>{{ active.title }}</h1><p class="doc-description">{{ active.turns.length }} 轮对话</p></header>
                <article v-for="turn in active.turns" :key="turn.id" class="conversation-turn">
                  <div class="question"><span class="avatar">你</span><div class="question-main"><div class="question-meta"><strong>{{ authUser.display_name }}</strong><span>{{ formatTime(turn.createdAt) }}</span></div><p>{{ turn.query }}</p></div></div>
                  <div class="answer-label">{{ turn.pending?'◌':'✓' }} AskData <span v-if="turn.pending" class="elapsed" role="status">{{ turn.progress }}</span></div>
                  <template v-if="turn.result">
                    <div v-if="turn.result.analysis_sources?.length" class="analysis-sources"><span v-for="source in turn.result.analysis_sources" :key="source.task_id">▦ {{ source.title }} · {{ source.row_count }} 行</span></div>
                    <details v-if="turn.result.interpretation" class="execution-details"><summary>本次查询口径</summary><p>数据表：{{ turn.result.interpretation.table }}<br>指标：{{ turn.result.interpretation.metric }} · 维度：{{ turn.result.interpretation.dimension }}<br>时间：{{ turn.result.interpretation.time_range }}</p><p v-for="assumption in turn.result.interpretation.assumptions" :key="assumption">{{ assumption }}</p></details>
                    <div v-if="turn.result.clarification" class="clarification-box"><p>{{ turn.result.clarification.question }}</p><small>{{ turn.result.clarification.reason }}</small><div class="clarification-options"><button v-for="option in turn.result.clarification.options" :key="option.id" class="btn" :disabled="loading||turn.resolved||!turn.canResume" @click="submit(option.label,undefined,{taskId:turn.result!.task_id,optionId:option.id,turn})">{{ option.label }}{{ option.recommended?' · 推荐':'' }}</button></div><small v-if="turn.resolved">已确认，后续结果见下方回复</small></div>
                    <p v-if="turn.result.status==='waiting_clarification'&&!turn.resolved&&!turn.canResume" class="context-warning">此任务已无法继续确认，请重新提问。<button class="btn" :disabled="loading" @click="submit(originalQuestion(turn))">重新提问</button></p>
                    <ResultTableCard v-if="turn.result.route==='database_query'&&turn.result.status==='completed'" :title="turn.result.result_title||'查询结果'" :columns="turn.result.columns" :rows="turn.result.rows" :updated-at="tableTime(turn)"><template #actions><button v-if="turn.result.sql" @click="toggleSql(turn.id)">SQL</button><button :disabled="turn.result.saved" @click="saveResult(turn.result)">{{ turn.result.saved?'已保存':'保存结果' }}</button></template><template #details><pre v-if="expandedSql.includes(turn.id)">{{ turn.result.sql }}</pre></template></ResultTableCard>
                    <ReportCard v-if="turn.result.report" :report="turn.result.report" :sources="reportSources" />
                    <MarkdownReport v-else-if="!turn.result.clarification&&(turn.result.analysis||turn.result.message)" :markdown="turn.result.analysis||turn.result.message" />
                    <p v-if="turn.result.status==='failed'" class="turn-error">{{ turn.result.message }}</p>
                    <details v-if="turn.result.steps.length" class="execution-details"><summary>查看处理过程</summary><ol><li v-for="step in turn.result.steps" :key="step">{{ step }}</li></ol></details>
                    <div v-if="turn.result.rows.length&&turn.result.status==='completed'" class="followup"><button :disabled="loading" @click="submit('解释这份结果',turn.result)">解释这份结果 ↗</button><button :disabled="loading" @click="submit('基于这份结果生成分析报告',turn.result)">生成分析报告 ↗</button></div>
                  </template>
                  <template v-else-if="turn.pending||turn.error"><ResultTableCard v-if="turn.table" title="查询结果" :columns="turn.table.columns" :rows="turn.table.rows" /><h2 v-if="turn.fields.title" class="stream-title">{{ turn.fields.title }}</h2><MarkdownReport v-if="streamingText(turn)" :markdown="streamingText(turn)" /><p v-else-if="turn.pending" class="pending-copy">{{ turn.progress }}</p></template>
                  <div v-if="turn.error" class="turn-error" role="alert">{{ turn.error }}<button class="btn" :disabled="loading" @click="retries.has(turn.id)?retries.get(turn.id)?.():submit(turn.query)">重试</button></div>
                </article>
              </template>
            </div>
          </div>
          <p v-if="error" class="error-banner" role="alert">{{ error }}<button aria-label="关闭提示" @click="error=''">×</button></p>
          <footer v-show="view==='chat'" class="composer-area"><div class="composer"><textarea v-model="input" :disabled="historyLoading" rows="2" aria-label="输入查询问题" placeholder="输入业务问题，或基于结果继续分析…" @keydown="onEnter"></textarea><div class="composer-toolbar"><span class="composer-hint">{{ selected.length }} 个字段 · {{ references.length }} 张参考表</span><button class="send" :disabled="loading||historyLoading||!input.trim()" aria-label="发送问题" @click="submit()">↑</button></div></div><div class="composer-note"><span>{{ loading?'正在生成回复…':'AskData Studio' }}</span><span>Enter 发送 · Shift + Enter 换行</span></div></footer>
        </section>
        <aside class="context-panel" :class="{open:mobileContext}" @dragover.prevent @drop.prevent="dropField"><div class="context-top"><strong>查询上下文</strong><button class="icon-button" aria-label="关闭上下文" @click="mobileContext=false">×</button></div><section class="context-section"><div class="section-header"><strong>查询字段</strong><span class="count">{{ selected.length }}</span><button class="context-clear" :disabled="!selected.length" @click="changeFields([])">清空</button></div><p class="context-description">指定本次查询需要使用的字段。</p><input v-if="selected.length" v-model="contextSearch" class="context-search" aria-label="搜索已选字段" placeholder="搜索已选字段"><div class="context-selection-list"><div v-for="f in contextFields" :key="`${f.tableId}:${f.name}`" class="field-item"><span class="type-mark">T</span><span class="field-name">{{ fieldLabel(f.tableId!,f.name) }}<small>{{ tableLabel(f.tableId!) }} · {{ f.name }}</small></span><button class="icon-button" :aria-label="`移除${fieldLabel(f.tableId!,f.name)}`" @click="removeField(f.tableId!,f.name)">×</button></div></div><p v-if="!selected.length" class="selection-empty">从数据目录添加字段，或拖动字段到这里。</p><p v-if="mixedDatabases" class="context-warning">已选择多个数据库的字段，当前查询仅支持单库。请按本次问题保留一个库的字段。</p><button class="add-field" @click="changeView('catalog');mobileContext=false">＋ 添加字段</button></section><section class="context-section"><div class="section-header"><strong>参考结果</strong><span class="count">{{ references.length }}</span></div><p class="context-description">用于解释已有结果和生成分析报告。</p><div v-for="m in references" :key="m.id" class="reference-item"><div class="reference-title">{{ m.title }}<button class="icon-button" aria-label="移除参考结果" @click="toggleReference(m.task_id!)">×</button></div><p>{{ m.rows?.length }} 行 · {{ m.columns?.join(' / ') }}</p></div><p v-if="!references.length" class="selection-empty">可从已存资产添加参考结果。</p><button class="add-field" @click="changeView('assets');mobileContext=false">＋ 选择已存结果</button></section><div class="context-tip">查询字段用于生成 SQL；参考结果用于已有数据的分析与比较。</div></aside>
      </div>
    </main><div v-if="toast" class="app-toast" role="status">{{ toast }}</div>
  </div>
</template>
