import importlib.util
from pathlib import Path
import yaml
spec=importlib.util.spec_from_file_location('migration','scripts/migrate-pipeline.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
load=yaml.load
calls=0
def record(text,**kwargs):
    global calls
    calls+=1
    if calls==2:
        Path('experiments/migration-debug.yml').write_text(text)
    return load(text,**kwargs)
module.yaml.load=record
module.migrate(Path('experiments/cpp-release-upstream.yml').read_text())
