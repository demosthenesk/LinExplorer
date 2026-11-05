#!/usr/bin/env bash
# LinExplorer - αντιγραφή των αρχείων του Explorer από το Win7 VDI.
# Το VDI ανοίγει ΜΟΝΟ για ανάγνωση (qemu-nbd -r, mount -o ro) - δεν αλλάζει τίποτα.
# Χρήση:  sudo bash /mnt/STORE/REA/LinExplorer/source/tools/extract-win7.sh
set -uo pipefail

VDI="/mnt/STORE/VMachines/Windows/Win7-64bit/Win7-64bit-en-TESTS-disk1.vdi"
OUT="/mnt/STORE/REA/LinExplorer/win7"
OWNER="${SUDO_USER:-$USER}"

[[ $EUID -eq 0 ]] || { echo "Τρέξε το με sudo."; exit 1; }
[[ -f "$VDI" ]]   || { echo "Δεν βρέθηκε το VDI: $VDI"; exit 1; }
if pgrep -f "VirtualBoxVM.*Win7-64bit" >/dev/null; then
  echo "Το VM Win7-64bit τρέχει - κάνε Shut down και ξανατρέξε."; exit 1
fi
command -v qemu-nbd >/dev/null || apt-get install -y qemu-utils || exit 1
modprobe nbd max_part=16 || exit 1

NBD=""
for d in /sys/block/nbd*; do
  [[ $(cat "$d/size") == 0 ]] && { NBD="/dev/$(basename "$d")"; break; }
done
[[ -n $NBD ]] || { echo "Δεν βρέθηκε ελεύθερη συσκευή nbd."; exit 1; }

MNT="$(mktemp -d /tmp/win7mnt.XXXX)"
cleanup() { umount "$MNT" 2>/dev/null; qemu-nbd -d "$NBD" >/dev/null 2>&1; rmdir "$MNT" 2>/dev/null; }
trap cleanup EXIT

echo ">> Σύνδεση VDI (μόνο ανάγνωση) στο $NBD"
qemu-nbd -r -c "$NBD" "$VDI" || exit 1
sleep 2; partprobe "$NBD" 2>/dev/null; sleep 1

FOUND=""
for p in "$NBD"p*; do
  [[ -b $p ]] || continue
  mount -o ro "$p" "$MNT" 2>/dev/null || mount -t ntfs-3g -o ro "$p" "$MNT" 2>/dev/null || continue
  if [[ -f "$MNT/Windows/explorer.exe" ]]; then FOUND="$p"; break; fi
  umount "$MNT"
done
[[ -n $FOUND ]] || { echo "Δεν βρέθηκε κατάτμηση με Windows."; exit 1; }
echo ">> Windows στην κατάτμηση $FOUND"

W="$MNT/Windows"; S="$W/System32"
MISSING=0
copy() {   # copy <φάκελος πηγής> <όνομα αρχείου> <υποφάκελος προορισμού>
  local f; f=$(find "$1" -maxdepth 1 -type f -iname "$2" -print -quit 2>/dev/null)
  if [[ -n $f ]]; then mkdir -p "$OUT/$3"; cp -v "$f" "$OUT/$3/"
  else echo "   (δεν υπάρχει: ${1#$MNT/}/$2)"; MISSING=$((MISSING+1)); fi
}

DLLS="ExplorerFrame.dll shell32.dll shellstyle.dll zipfldr.dll propsys.dll ntshrui.dll SearchFolder.dll"

echo ">> Εκτελέσιμα / DLL"
copy "$W" explorer.exe Windows
for n in $DLLS; do copy "$S" "$n" Windows/System32; done
copy "$W/Resources/Themes/Aero/Shell/NormalColor" shellstyle.dll Windows/Resources/Themes/Aero/Shell/NormalColor

echo ">> Αρχεία γλώσσας (.mui)"
while IFS= read -r L; do
  copy "$L" explorer.exe.mui "Windows/$(basename "$L")"
done < <(find "$W" -maxdepth 1 -type d -iname '??-??')
while IFS= read -r L; do
  for n in $DLLS; do copy "$L" "$n.mui" "Windows/System32/$(basename "$L")"; done
done < <(find "$S" -maxdepth 1 -type d -iname '??-??')

( cd "$OUT" && find . -type f ! -name MANIFEST.txt -print0 | sort -z | xargs -0 sha256sum > MANIFEST.txt )
chown -R "$OWNER": "$OUT"

echo
echo ">> Τέλος. Αρχεία στο $OUT ($(($(wc -l < "$OUT/MANIFEST.txt"))) αρχεία, $MISSING δεν βρέθηκαν - φυσιολογικό για γλώσσες χωρίς μετάφραση)."
