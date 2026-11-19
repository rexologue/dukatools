# Writing to NTFS drives on macOS (FUSE-T + ntfs-3g)

macOS mounts NTFS volumes **read-only**. This page is the manual recipe that
`duka ntfs` automates, plus everything learned while testing it on a real
2 TB USB HDD (macOS 26, Apple Silicon). Follow it by hand if you want to see
every step, or use it to review what `duka ntfs setup` / `duka ntfs mount` do.

- No kernel extension and no Reduced Security mode.
- Only open-source code runs as root (ntfs-3g, GPL). FUSE-T itself is a free but
  **closed-source** binary, free for non-commercial use
  ([license](https://github.com/macos-fuse-t/fuse-t/blob/main/License.txt)).
- Tested with FUSE-T 1.2.9 and ntfs-3g 2026.9.28.

## How it works

```
Finder / apps ──NFS (localhost)──> FUSE-T ──FUSE──> ntfs-3g (root) ──> /dev/diskXsY
```

FUSE-T exposes the filesystem to macOS as a local NFS share, ntfs-3g does the
actual NTFS reading and writing on the raw partition.

## 1. Install FUSE-T

Needs your admin password (it installs a `.pkg`):

```bash
brew install --cask macos-fuse-t/cask/fuse-t
```

It installs `/usr/local/lib/libfuse-t.dylib`, headers and
`/usr/local/lib/pkgconfig/fuse-t.pc`. No kext is loaded.

## 2. Build tools

```bash
xcode-select --install   # if `xcode-select -p` fails
brew install pkgconf
```

`autoconf` is not needed: the release tarball ships a ready `configure`.

## 3. Build ntfs-3g against FUSE-T

There is no ready-made FUSE-T build of ntfs-3g. Homebrew's `gromgit/fuse/ntfs-3g-mac`
links macFUSE (kext) and no longer ships bottles for recent macOS.

```bash
mkdir -p ~/src/ntfs-3g && cd ~/src/ntfs-3g
curl -fLO https://tuxera.com/opensource/ntfs-3g_ntfsprogs-2026.9.28.tgz
echo "350d9415c59f7c3fa74e23985ad2d56423c6a168337368f672e2e362d8f295c8  ntfs-3g_ntfsprogs-2026.9.28.tgz" | shasum -a 256 -c
tar xf ntfs-3g_ntfsprogs-2026.9.28.tgz && cd ntfs-3g-2026.9.28
FUSE_MODULE_CFLAGS="$(PKG_CONFIG_PATH=/usr/local/lib/pkgconfig pkg-config --cflags fuse-t)" \
FUSE_MODULE_LIBS="$(PKG_CONFIG_PATH=/usr/local/lib/pkgconfig pkg-config --libs fuse-t)" \
CPPFLAGS="-D_FILE_OFFSET_BITS=64" \
./configure --with-fuse=external --disable-shared --enable-static --disable-ldconfig
make -j"$(sysctl -n hw.ncpu)"
otool -L src/ntfs-3g   # must show @rpath/libfuse-t.dylib and no macFUSE
```

The checksum comes from the official release notes on
[github.com/tuxera/ntfs-3g](https://github.com/tuxera/ntfs-3g/releases).
`FUSE_MODULE_CFLAGS` / `FUSE_MODULE_LIBS` are configure's official overrides; they
pin the build to FUSE-T even when macFUSE is also installed.

## 4. Install the two binaries as root

```bash
sudo install -m 755 -o root -g wheel src/ntfs-3g ntfsprogs/ntfsfix /usr/local/bin/
```

Mounting runs ntfs-3g through `sudo`, so it must live in a root-owned location.
Running a binary from your home directory with sudo would let anything running as
your user replace it and get root. For the same reason, never run `duka` itself
with sudo.

## 5. Mount read/write

Find the partition (`diskutil list external`), here `disk4s2`:

```bash
diskutil unmount /dev/disk4s2           # macOS keeps the device busy while its read-only mount exists
sudo ntfsfix -n /dev/disk4s2            # read-only check, writes nothing
sudo mkdir -p /Volumes/ADATA            # macOS deletes this directory on every unmount
sudo ntfs-3g /dev/disk4s2 /Volumes/ADATA -o uid=$(id -u),gid=$(id -g),umask=022,windows_names,hide_dot_files,volname=ADATA
```

| Option | Why |
|---|---|
| `uid`, `gid`, `umask=022` | Files belong to you, otherwise Finder cannot write. |
| `windows_names` | Refuse names Windows cannot open (`: ? * " < > \|`, trailing dot or space). |
| `hide_dot_files` | Newly created dot-files (including `._*`) get the NTFS *hidden* attribute, so Windows does not show them. |
| `volname` | Name shown in Finder. Keep it ASCII without spaces. |

`ntfsfix -n` reports `Resource busy` and *"Volume is corrupt"* if you skip the
`diskutil unmount`. That message is misleading: it never got to the check.

If Windows left the volume hibernated (Fast Startup) or dirty, ntfs-3g refuses a
read/write mount. Boot Windows, run `chkdsk /f`, disable Fast Startup and eject
the drive safely. Do not force it.

## 6. Unmount

```bash
sudo umount /Volumes/ADATA
diskutil eject disk4        # whole disk, before unplugging
```

Wait until the `ntfs-3g` process has exited (`pgrep -fl ntfs-3g`) before
unplugging: it marks the volume clean on exit.

## Known limitations

**Extended attributes are stored as `._` files.** FUSE-T's NFS backend never
forwards xattr calls to ntfs-3g (verified with `ntfs-3g -o debug`: no SETXATTR
ever arrives). The macOS NFS client therefore stores xattrs such as quarantine
flags and Finder tags in AppleDouble `._name` files next to each file.
`hide_dot_files` hides them from Windows, and `dot_clean /Volumes/ADATA` removes them.
The alternative, FUSE-T's `noappledouble`, makes every xattr write fail with
`Operation not permitted`. Finder can then abort copies with error -36, so it is
not used. `user_xattr` has no effect in this setup.

**Writes are slow on HDDs.** Measured on a USB 3 2.5" HDD:

| Path | Speed |
|---|---|
| raw read, `/dev/rdisk4s2` | 128 MB/s |
| buffered read, `/dev/disk4s2` | 64 MB/s |
| read through the mount | 62 MB/s |
| sustained write through the mount | 5–14 MB/s (short bursts up to ~30) |

`fs_usage` shows why. For every 32 KB data write, ntfs-3g also updates the MFT
record (1 KB) and allocation bitmaps (4 KB and single bytes at unaligned offsets).
These go through the buffered block device, which macOS flushes in 16 KB blocks,
so the disk head keeps seeking between metadata and data. It is *not*
`F_FULLFSYNC`: those calls take microseconds. ntfs-3g cannot use the fast
`/dev/rdisk*` device because raw devices require sector-aligned I/O and ntfs-3g
does 1-byte writes. Changing that would mean patching ntfs-3g. `rwsize=1048576`
made writes slower, about 3 MB/s.

**Other FUSE-T backends do not work with ntfs-3g (FUSE-T 1.2.9):**

- `backend=smb`: writes fail with `Input/output error`, and ntfs-3g keeps running
  after unmount.
- `backend=fskit` (macOS 26+, enable *fuse-t* under System Settings → General →
  Login Items & Extensions → File System Extensions): ntfs-3g and lowntfs-3g
  unmount themselves right after mounting, and every access fails with
  `Connection reset by peer`.

**Automount.** macOS mounts the drive read-only again every time it is plugged
in. Run `duka ntfs mount` (or step 5) after connecting it.

**Spin-up.** A sleeping HDD takes about 3 s to answer the first request.

## Uninstall

```bash
sudo rm /usr/local/bin/ntfs-3g /usr/local/bin/ntfsfix
brew uninstall --cask fuse-t
brew untap macos-fuse-t/cask
```

`brew uninstall --cask fuse-t` removes the app and its servers in
`/Library/Application Support/fuse-t`, but leaves the library and headers it
installed in `/usr/local` behind. To remove them too:

```bash
sudo rm /usr/local/lib/libfuse-t* /usr/local/lib/libfuse3* /usr/local/lib/pkgconfig/fuse-t.pc /usr/local/lib/pkgconfig/fuse3.pc
sudo rm -r /usr/local/include/fuse /usr/local/include/fuse3
```
