"""Compare current regression cases against the captured old engine and current engine."""
import argparse,importlib.util,unittest,sys
from evaluation.run import OUT,write

p=argparse.ArgumentParser();p.add_argument('--before',action='store_true');args=p.parse_args()
from tests import test_mcp_runtime
if args.before:
    path=OUT/'r0-live/source/app/querying/duckdb_engine.py'
    spec=importlib.util.spec_from_file_location('app.querying._evaluation_old_engine',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    test_mcp_runtime.DuckDbEngine=module.DuckDbEngine
suite=unittest.defaultTestLoader.loadTestsFromModule(test_mcp_runtime)
label='before' if args.before else 'after'
with (OUT/f'cte-tests-{label}.txt').open('w',encoding='utf8') as log:
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
write(OUT/f'cte-tests-{label}.json',{'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'successful':result.wasSuccessful(),'failed_tests':[str(t) for t,_ in result.failures],'engine':'captured r0 source' if args.before else 'current source'})
print({'label':label,'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors)},flush=True)
sys.exit(0 if result.wasSuccessful() or (args.before and len(result.failures)==2 and not result.errors) else 1)
