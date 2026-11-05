"""Dump Win32 resources (menus, dialogs, strings, accelerators, UIFILE/XML/RCDATA) to text/JSON."""
import pefile, struct, sys, os, json
RT = pefile.RESOURCE_TYPE; INV = {v: k for k, v in RT.items()}

def wstr(b, o):
    e = o
    while b[e:e+2] != b'\0\0': e += 2
    return b[o:e].decode('utf-16le', 'replace'), e + 2

def sz_or_ord(b, o):
    w = struct.unpack_from('<H', b, o)[0]
    if w == 0: return '', o + 2
    if w == 0xFFFF: return struct.unpack_from('<H', b, o+2)[0], o + 4
    return wstr(b, o)

align4 = lambda o: (o + 3) & ~3

def parse_menu(b):
    ver, hsz = struct.unpack_from('<HH', b, 0)
    out = []
    if ver == 0:
        o = 4
        def items(o, depth):
            while True:
                fl = struct.unpack_from('<H', b, o)[0]; o += 2
                if fl & 0x10:  # MF_POPUP
                    t, o = wstr(b, o); out.append(('  '*depth) + f'[POPUP] {t}')
                    o = items(o, depth+1)
                else:
                    mid = struct.unpack_from('<H', b, o)[0]; o += 2
                    t, o = wstr(b, o)
                    out.append(('  '*depth) + (f'{mid:>6}  {t}' if (t or mid) else '------'))
                if fl & 0x80: return o
        items(o, 0)
    else:  # MENUEX
        o = 4 + hsz
        def items(o, depth):
            while True:
                typ, st, mid, res = struct.unpack_from('<IIIH', b, o); o += 14
                t, o = wstr(b, o); o = align4(o)
                if typ & 0x800: out.append(('  '*depth) + '------')
                elif res & 1:
                    out.append(('  '*depth) + f'[POPUP {mid}] {t}')
                    o += 4; o = items(o, depth+1)
                else: out.append(('  '*depth) + f'{mid:>6}  {t}')
                if res & 1 and False: pass
                if res & 0x80: return o
        items(o, 0)
    return out

CLS = {0x80:'BUTTON',0x81:'EDIT',0x82:'STATIC',0x83:'LISTBOX',0x84:'SCROLLBAR',0x85:'COMBOBOX'}
def parse_dialog(b):
    sig = struct.unpack_from('<HH', b, 0)
    ex = sig == (1, 0xFFFF)
    if ex:
        _, _, help_, exst, st, n, x, y, cx, cy = struct.unpack_from('<HHIIIHhhhh', b, 0); o = 26
    else:
        st, exst, n, x, y, cx, cy = struct.unpack_from('<IIHhhhh', b, 0); o = 18
    menu, o = sz_or_ord(b, o); cls, o = sz_or_ord(b, o); title, o = wstr(b, o)
    font = None
    if st & 0x40 or st & 0x48:
        if ex:
            pt, wt, it, cs = struct.unpack_from('<HHBB', b, o); o += 6
        else:
            pt = struct.unpack_from('<H', b, o)[0]; o += 2
        face, o = wstr(b, o); font = f'{pt}pt {face}'
    d = {'title': title, 'size': [cx, cy], 'style': hex(st), 'font': font, 'controls': []}
    for i in range(n):
        o = align4(o)
        if ex:
            hid, cexst, cst, x, y, w, h, cid = struct.unpack_from('<IIIhhhhI', b, o); o += 24
        else:
            cst, cexst, x, y, w, h, cid = struct.unpack_from('<IIhhhhH', b, o); o += 18
        c, o = sz_or_ord(b, o); t, o = sz_or_ord(b, o)
        extra = struct.unpack_from('<H', b, o)[0]; o += 2 + extra
        c = CLS.get(c, c) if isinstance(c, int) else c
        if cid == 0xFFFF or cid == 0xFFFFFFFF: cid = -1
        d['controls'].append({'id': cid, 'class': c, 'text': t, 'rect': [x, y, w, h], 'style': hex(cst)})
    return d

def parse_strings(blockid, b):
    res = {}; o = 0
    for i in range(16):
        n = struct.unpack_from('<H', b, o)[0]; o += 2
        if n: res[(blockid-1)*16+i] = b[o:o+2*n].decode('utf-16le', 'replace')
        o += 2*n
    return res

VK = {0x08:'Back',0x09:'Tab',0x0D:'Enter',0x1B:'Esc',0x20:'Space',0x21:'PgUp',0x22:'PgDn',0x23:'End',0x24:'Home',0x25:'Left',0x26:'Up',0x27:'Right',0x28:'Down',0x2D:'Ins',0x2E:'Del',0xBB:'=',0xBD:'-',0xBF:'/',0xDC:'\\',0xC0:'`',0xA6:'BrowserBack',0xA7:'BrowserFwd',0xA8:'BrowserRefresh',0xAA:'BrowserSearch',0xAB:'BrowserFav',0xAC:'BrowserHome',0x6B:'Num+',0x6D:'Num-',0x6A:'Num*',0x6F:'Num/'}
VK.update({0x70+i: f'F{i+1}' for i in range(24)})
def parse_accel(b):
    out = []
    for o in range(0, len(b) - 7, 8):
        fl, key, cid, _ = struct.unpack_from('<HHHH', b, o)
        mods = ''.join(m for m, bit in (('Ctrl+',8), ('Alt+',16), ('Shift+',4)) if fl & bit)
        k = (VK.get(key, chr(key) if 32 < key < 127 else hex(key))) if fl & 1 else repr(chr(key))
        out.append(f'{cid:>6}  {mods}{k}')
        if fl & 0x80: break
    return out

def rname(e): return str(e.name) if e.name else e.id

def dump(path, outdir):
    pe = pefile.PE(path, fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
    os.makedirs(outdir, exist_ok=True)
    strings, menus, dialogs, accels = {}, {}, {}, {}
    for t in getattr(pe, 'DIRECTORY_ENTRY_RESOURCE', type('', (), {'entries': []})).entries:
        tname = str(t.name) if t.name else INV.get(t.id, t.id)
        for r in t.directory.entries:
            for lang in r.directory.entries:
                d = lang.data.struct
                b = pe.get_data(d.OffsetToData, d.Size)
                rid = rname(r)
                try:
                    if tname == 'RT_STRING': strings.update(parse_strings(rid, b))
                    elif tname == 'RT_MENU': menus[rid] = parse_menu(b)
                    elif tname == 'RT_DIALOG': dialogs[rid] = parse_dialog(b)
                    elif tname == 'RT_ACCELERATOR': accels[rid] = parse_accel(b)
                    elif tname in ('UIFILE', 'XML', 'RT_RCDATA', 'RT_HTML', 'FTR', 'LIBRARY', 'ORDERSTREAM'):
                        ext = 'bin'
                        if b[:2] in (b'\xff\xfe',) or (len(b) > 3 and b[1] == 0 and b[3] == 0): txt = b.decode('utf-16le', 'replace').lstrip('\ufeff'); ext = 'txt'
                        elif b[:1] in (b'<',) or b[:3] == b'\xef\xbb\xbf': txt = b.decode('utf-8', 'replace'); ext = 'txt'
                        os.makedirs(f'{outdir}/{tname}', exist_ok=True)
                        if ext == 'txt': open(f'{outdir}/{tname}/{rid}.txt', 'w').write(txt)
                        else: open(f'{outdir}/{tname}/{rid}.bin', 'wb').write(b)
                except Exception as ex:
                    print('  ! ', tname, rid, ex)
    if strings: json.dump({str(k): v for k, v in sorted(strings.items())}, open(f'{outdir}/strings.json', 'w'), ensure_ascii=False, indent=0)
    if menus:
        with open(f'{outdir}/menus.txt', 'w') as f:
            for k, v in sorted(menus.items(), key=lambda x: str(x[0])): f.write(f'=== MENU {k}\n' + '\n'.join(v) + '\n\n')
    if dialogs: json.dump({str(k): v for k, v in dialogs.items()}, open(f'{outdir}/dialogs.json', 'w'), ensure_ascii=False, indent=1)
    if accels:
        with open(f'{outdir}/accelerators.txt', 'w') as f:
            for k, v in accels.items(): f.write(f'=== ACCEL {k}\n' + '\n'.join(v) + '\n\n')
    print(os.path.basename(path), 'strings', len(strings), 'menus', len(menus), 'dialogs', len(dialogs), 'accels', len(accels))

if __name__ == '__main__':
    dump(sys.argv[1], sys.argv[2])
