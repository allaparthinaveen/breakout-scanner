import yaml
def load_config(path='config/default.yaml'):
 with open(path,encoding='utf-8') as f:return yaml.safe_load(f)
