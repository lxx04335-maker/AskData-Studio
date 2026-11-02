"""Keep the original 28 cases unchanged; add two explicitly white-box edge tests."""
from evaluation.run import EVAL,read,write
base=read(EVAL/'cases.v1.json')
extension=[
 {'id':'G29','title':'CTE同名遮蔽不能绕过表权限','kind':'guard_cte_shadow'},
 {'id':'G30','title':'空表权限同名CTE仍需拒绝','kind':'guard_cte_empty_scope'},
]
write(EVAL/'cases.v1.1.json',{'version':'1.1','note':'Original 28 unchanged; two source-informed CTE alias shadowing edge tests added before any application fix. Not an unbiased held-out benchmark.','cases':base['cases']+extension})
print('Frozen 30 cases; original 28 remain byte-equivalent as task objects.')
