import shutil
import os

print('SCWRL4 / AutoDock Vina environment check')
print('-----------------------------------------')
for name in ['Scwrl4', 'vina', 'obabel', 'babel']:
    path = shutil.which(name)
    print(f'{name}:', path if path else 'NOT FOUND')

print('\nEnvironment variables:')
for var in ['PATH', 'CONDA_PREFIX', 'MINIPROT_ENV']:
    print(f'{var}:', os.environ.get(var, 'not set'))
