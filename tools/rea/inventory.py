import pefile, sys, glob, os, collections
R = pefile.RESOURCE_TYPE
inv = {v:k for k,v in R.items()}
for f in sys.argv[1:]:
    pe = pefile.PE(f, fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
    c = collections.Counter()
    if hasattr(pe,'DIRECTORY_ENTRY_RESOURCE'):
        for t in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            name = str(t.name) if t.name else inv.get(t.id, t.id)
            c[name] = len(t.directory.entries)
    print(os.path.relpath(f,'/mnt/user-data/uploads/LinExplorer/win7'), dict(c))
