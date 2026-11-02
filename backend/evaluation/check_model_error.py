"""Replay exactly one failed model request to retain the upstream HTTP error body."""
import json,time,urllib.request,urllib.error
from app.config import settings
from evaluation.run import OUT,read,write

case=read(OUT/'r1-scope/cases/Q25.json')
call=next(c for c in case['model_calls'] if c.get('error') and c['type']=='chat_json')
payload={'model':settings.llm_model,'messages':[{'role':'system','content':call['system']},{'role':'user','content':call['user']}],'temperature':settings.temperature,'stream':True}
request=urllib.request.Request(settings.chat_url,data=json.dumps(payload,ensure_ascii=False).encode('utf8'),headers={'Authorization':'Bearer '+settings.api_key,'Content-Type':'application/json'},method='POST')
start=time.perf_counter()
try:
    with urllib.request.urlopen(request,timeout=settings.timeout) as response:
        lines=[]
        for line in response:
            value=line.decode('utf8').strip()
            if value:lines.append(value)
            if len(lines)>=2:break
        result={'http_status':response.status,'initial_events':lines,'note':'Only connectivity/error-body diagnosis; not counted as a completed task.'}
except urllib.error.HTTPError as exc:
    result={'http_status':exc.code,'body':exc.read().decode('utf8',errors='replace')[:3000]}
except Exception as exc:result={'error':str(exc)}
result['seconds']=round(time.perf_counter()-start,3)
write(OUT/'upstream-error-diagnosis.json',result)
print(json.dumps(result,ensure_ascii=False))
