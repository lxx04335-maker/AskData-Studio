"""Build a fixed, versioned interview evaluation set. No model-generated gold answers."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
CASES = []

def sql(i, title, database, query, gold, fields=()):
    CASES.append(dict(id=f'Q{i:02}', title=title, kind='query', database=database,
        user='demo_analyst' if database=='askdata_mock' else 'demo_current_sales',
        query=f'只查询 {database}：'+query, gold_sql=gold,
        required_fields=list(fields), tolerance=0.011))

M='askdata_mock'; E='ecommerce_ops'
sql(1,'销售总览',M,'统计2026年8月全部订单数和实付金额合计，不额外筛选订单状态。',
    "SELECT COUNT(order_id), ROUND(SUM(paid_amount),2) FROM orders_current WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",['orders_current.order_id','orders_current.paid_amount','orders_current.order_date'])
sql(2,'客户等级销售额',M,'查询2026年8月各客户等级的销售额，按实付金额汇总，从高到低排序，不额外筛选订单状态。',
    "SELECT c.customer_level,ROUND(SUM(o.paid_amount),2) FROM orders_current o JOIN customers c ON o.customer_id=c.customer_id WHERE o.order_date>=DATE '2026-08-01' AND o.order_date<DATE '2026-09-01' GROUP BY c.customer_level ORDER BY 2 DESC",['customers.customer_level','orders_current.paid_amount','orders_current.order_date'])
sql(3,'地区销售额',M,'统计2026年8月实付金额大于0的订单，按订单地区汇总实付金额。',
    "SELECT region,ROUND(SUM(paid_amount),2) FROM orders_current WHERE paid_amount>0 AND order_date>=DATE '2026-08-01' AND order_date<DATE '2026-09-01' GROUP BY region",['orders_current.region','orders_current.paid_amount','orders_current.order_date'])
sql(4,'历史月度趋势',M,'查询2026年6月和7月各月实付金额合计，月份显示为YYYY-MM，不额外筛选状态。',
    "SELECT strftime(order_date,'%Y-%m'),ROUND(SUM(paid_amount),2) FROM orders_history WHERE order_date>=DATE '2026-06-01' AND order_date<DATE '2026-08-01' GROUP BY 1 ORDER BY 1",['orders_history.order_date','orders_history.paid_amount'])
sql(5,'商品品类销售',M,'统计2026年8月各商品品类的实付金额合计，不额外筛选订单状态。',
    "SELECT p.category,ROUND(SUM(o.paid_amount),2) FROM orders_current o JOIN products p ON o.product_id=p.product_id WHERE o.order_date>=DATE '2026-08-01' AND o.order_date<DATE '2026-09-01' GROUP BY p.category",['products.category','orders_current.paid_amount'])
sql(6,'客户销售Top5',M,'统计2026年8月实付金额合计最高的5个客户，返回客户名称和实付金额合计，不额外筛选订单状态。',
    "SELECT c.customer_name,ROUND(SUM(o.paid_amount),2) FROM orders_current o JOIN customers c ON o.customer_id=c.customer_id WHERE o.order_date>=DATE '2026-08-01' AND o.order_date<DATE '2026-09-01' GROUP BY c.customer_name ORDER BY 2 DESC LIMIT 5",['customers.customer_name','orders_current.paid_amount'])

D="o.ordered_at >= TIMESTAMP '2026-08-01' AND o.ordered_at < TIMESTAMP '2026-08-23'"
sql(7,'电商支付总览',E,'统计2026年8月1日至22日下单且实付金额大于0的订单数和实付金额合计。',
    f'SELECT COUNT(o.order_id),ROUND(SUM(o.paid_amount),2) FROM orders o WHERE {D} AND o.paid_amount>0',['ecommerce_ops.orders.ordered_at','ecommerce_ops.orders.paid_amount'])
sql(8,'店铺销售Top5',E,'统计2026年8月1日至22日下单且实付金额大于0的订单，返回销售额最高的5家店铺名称和实付金额合计。',
    f'SELECT s.store_name,ROUND(SUM(o.paid_amount),2) FROM orders o JOIN stores s ON o.store_id=s.store_id WHERE {D} AND o.paid_amount>0 GROUP BY s.store_name ORDER BY 2 DESC LIMIT 5',['ecommerce_ops.stores.store_name','ecommerce_ops.orders.paid_amount'])
sql(9,'类目明细销售额',E,'统计2026年8月1日至22日下单且订单实付金额大于0的订单，按商品类目名称汇总订单明细实付金额，取金额最高的10类。',
    f'SELECT c.category_name,ROUND(SUM(i.paid_amount),2) FROM orders o JOIN order_items i ON o.order_id=i.order_id JOIN products p ON i.product_id=p.product_id JOIN product_categories c ON p.category_id=c.category_id WHERE {D} AND o.paid_amount>0 GROUP BY c.category_name ORDER BY 2 DESC LIMIT 10',['ecommerce_ops.order_items.paid_amount','ecommerce_ops.product_categories.category_name','ecommerce_ops.orders.ordered_at'])
sql(10,'渠道销售额',E,'统计2026年8月1日至22日下单且实付金额大于0的订单，按订单归因渠道名称汇总实付金额。',
    f'SELECT c.channel_name,ROUND(SUM(o.paid_amount),2) FROM orders o JOIN acquisition_channels c ON o.channel_id=c.channel_id WHERE {D} AND o.paid_amount>0 GROUP BY c.channel_name',['ecommerce_ops.orders.channel_id','ecommerce_ops.acquisition_channels.channel_name','ecommerce_ops.orders.paid_amount'])
sql(11,'广告收入成本比',E,'汇总2026年8月1日至22日的广告归因收入和广告成本，并计算总归因收入除以总广告成本，比例保留4位小数。',
    "SELECT ROUND(SUM(attributed_revenue),2),ROUND(SUM(ad_cost),2),ROUND(SUM(attributed_revenue)/NULLIF(SUM(ad_cost),0),4) FROM ad_daily_stats WHERE stat_date>=DATE '2026-08-01' AND stat_date<DATE '2026-08-23'",['ecommerce_ops.ad_daily_stats.attributed_revenue','ecommerce_ops.ad_daily_stats.ad_cost','ecommerce_ops.ad_daily_stats.stat_date'])
sql(12,'退款订单去重',E,'对2026年8月1日至22日下单且实付金额大于0的订单，统计其中有退款记录的去重订单数、全部符合条件的订单数，以及前者除以后者的比例，保留4位小数；退款记录不限制申请时间和状态。',
    f'SELECT COUNT(DISTINCT r.order_id),COUNT(DISTINCT o.order_id),ROUND(COUNT(DISTINCT r.order_id)*1.0/NULLIF(COUNT(DISTINCT o.order_id),0),4) FROM orders o LEFT JOIN refunds r ON o.order_id=r.order_id WHERE {D} AND o.paid_amount>0',['ecommerce_ops.orders.ordered_at','ecommerce_ops.orders.paid_amount','ecommerce_ops.refunds.order_id'])
sql(13,'店铺目标进度',E,'查询2026年8月1日至22日每日店铺指标的GMV合计最高的5家店铺，返回店铺名称、该期间GMV、8月月度GMV目标和完成率，完成率为实际除以目标，保留4位小数。',
    "WITH a AS (SELECT store_id,SUM(gmv) g FROM daily_store_metrics WHERE stat_date>=DATE '2026-08-01' AND stat_date<DATE '2026-08-23' GROUP BY store_id) SELECT s.store_name,ROUND(a.g,2),t.gmv_target,ROUND(a.g/NULLIF(t.gmv_target,0),4) FROM a JOIN sales_targets t ON a.store_id=t.store_id AND t.target_month='2026-08' JOIN stores s ON a.store_id=s.store_id ORDER BY a.g DESC LIMIT 5",['ecommerce_ops.daily_store_metrics.gmv','ecommerce_ops.sales_targets.gmv_target','ecommerce_ops.stores.store_name'])
sql(14,'订单状态分布',E,'统计2026年8月1日至22日下单的订单，按订单状态统计订单数，不额外筛选实付金额。',
    f'SELECT o.order_status,COUNT(o.order_id) FROM orders o WHERE {D} GROUP BY o.order_status',['ecommerce_ops.orders.order_status','ecommerce_ops.orders.ordered_at'])
sql(15,'注册平台分布',E,'统计2026年8月1日至22日注册的用户，按注册平台汇总用户数。',
    "SELECT register_platform,COUNT(user_id) FROM users WHERE registered_at>=TIMESTAMP '2026-08-01' AND registered_at<TIMESTAMP '2026-08-23' GROUP BY register_platform",['ecommerce_ops.users.register_platform','ecommerce_ops.users.registered_at'])
sql(16,'客单价',E,'计算2026年8月1日至22日下单且实付金额大于0的订单客单价，定义为订单实付金额合计除以订单数，只返回客单价，保留2位小数。',
    f'SELECT ROUND(SUM(o.paid_amount)/NULLIF(COUNT(o.order_id),0),2) FROM orders o WHERE {D} AND o.paid_amount>0',['ecommerce_ops.orders.paid_amount','ecommerce_ops.orders.order_id','ecommerce_ops.orders.ordered_at'])
sql(17,'一对多关联避免重复',E,'统计2026年8月1日至22日下单、实付金额大于0且至少有一条订单明细的订单，返回订单数和订单级实付金额合计；每个订单只计算一次。',
    f'SELECT COUNT(o.order_id),ROUND(SUM(o.paid_amount),2) FROM orders o WHERE {D} AND o.paid_amount>0 AND EXISTS (SELECT 1 FROM order_items i WHERE i.order_id=o.order_id)',['ecommerce_ops.orders.paid_amount','ecommerce_ops.order_items.order_id'])
sql(18,'同类目订单去重汇总',E,'对2026年8月1日至22日下单且实付金额大于0的订单，按商品类目名称统计订单级实付金额合计；同一个订单在同一个类目只计算一次，取金额最高的5类。',
    f'WITH a AS (SELECT DISTINCT o.order_id,o.paid_amount,c.category_name FROM orders o JOIN order_items i ON o.order_id=i.order_id JOIN products p ON i.product_id=p.product_id JOIN product_categories c ON p.category_id=c.category_id WHERE {D} AND o.paid_amount>0) SELECT category_name,ROUND(SUM(paid_amount),2) FROM a GROUP BY category_name ORDER BY 2 DESC LIMIT 5',['ecommerce_ops.orders.paid_amount','ecommerce_ops.product_categories.category_name','ecommerce_ops.order_items.order_id'])
sql(19,'合法空结果',M,'查询2027年8月各订单地区的实付金额合计，直接按现有数据查询，没有数据就返回空结果，不改查其他日期。',
    "SELECT region,ROUND(SUM(paid_amount),2) FROM orders_current WHERE order_date>=DATE '2027-08-01' AND order_date<DATE '2027-09-01' GROUP BY region",['orders_current.order_date','orders_current.region','orders_current.paid_amount'])

CASES.extend([
 dict(id='Q20',title='不存在指标不编造',kind='unsupported',user='demo_analyst',query='只查询 askdata_mock：统计2026年8月各仓库的库存周转天数。'),
 dict(id='Q21',title='问候不查库',kind='greeting',user='demo_analyst',query='你好，介绍一下你能做什么。'),
 dict(id='Q22',title='连续解释复用来源',kind='explain',user='demo_analyst',setup='Q02',query='解释刚才这份结果，不要查询新数据。'),
 dict(id='Q23',title='报告图表引用原表',kind='report',user='demo_analyst',setup='Q02',query='基于刚才的客户等级销售结果生成分析报告，并用柱状图展示，引用原表，不要查询新数据。'),
 dict(id='Q24',title='信息不足先澄清',kind='clarify',user='demo_analyst',query='帮我看看销售情况。'),
 dict(id='Q25',title='工作区越权字段拦截',kind='workspace_denied',user='demo_analyst',query='只查询 askdata_mock：统计2026年8月订单实付金额合计。',workspace={'schema_fields':[{'tableId':'ecommerce_ops.orders','name':'paid_amount'}]}),
 dict(id='G26',title='写入和多语句拒绝',kind='guard_write'),
 dict(id='G27',title='CTE内部越权表拒绝',kind='guard_cte'),
 dict(id='G28',title='未登录与目录范围',kind='guard_auth'),
])

if __name__=='__main__':
    ROOT.mkdir(exist_ok=True)
    (ROOT/'cases.v1.json').write_text(json.dumps({'version':'1.0','cases':CASES},ensure_ascii=False,indent=2),encoding='utf8')
    print(f'Fixed cases: {len(CASES)}; real-model tasks: 25; deterministic guardrails: 3')
