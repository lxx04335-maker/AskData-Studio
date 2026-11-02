"""Package the evidence and record the exact regression outcome without new model calls."""
from evaluation.run import OUT,BACKEND,read,write,digest
import re,zipfile

text=(OUT/'full-regression-after.txt').read_text(encoding='utf8',errors='replace')
match=re.search(r'Ran (\d+) tests in ([\d.]+)s',text)
assert match and re.search(r'\bOK\s*$',text)
write(OUT/'full-regression-after.json',{'tests':int(match[1]),'seconds':float(match[2]),'successful':True,'log':'full-regression-after.txt','application_engine_sha256':digest(BACKEND/'app/querying/duckdb_engine.py')})
for name in ['r0-live','r0-edge','r1-scope','r2-confirm']:
    round_dir=OUT/name
    if name=='r2-confirm':
        assert all(not read(p).get('model_calls') for p in (round_dir/'cases').glob('*.json'))
write(OUT/'completion.json',{'task_count':30,'application_changes':['backend/app/querying/duckdb_engine.py'],'regression_tests':71,
    'resolved':['G29','G30'],'open_application_issue':['Q12 time-field retrieval omission'],
    'model_retests_blocked':['Q04','Q12','Q22','Q23','Q24','Q25'],
    'provider_error':'Exact Q25 replay returned HTTP 400 Arrearage; no further model calls were made after diagnosis.',
    'r2_no_model_calls':True,'all_30_attempted_in_r1':True,'all_30_passed':False})
archive=OUT/'AskData-测试与复盘.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for path in sorted(OUT.rglob('*')):
        if not path.is_file() or path==archive or path.name=='schema-cache.json' or path.suffix in ('.zip','.png'):continue
        z.write(path,path.relative_to(OUT))
    for path in sorted((BACKEND/'evaluation').glob('*')):
        if path.is_file():z.write(path,'evaluation-tools/'+path.name)
print({'archive':str(archive),'bytes':archive.stat().st_size,'model_calls_after_arrearage':0})
