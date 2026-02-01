# PJSIP Installation on Windows

## Requirements

### Operating System

Windows NT, 2000, XP, 2003, Vista, 7, 10, 11

### Visual Studio

Supported versions:
- Visual Studio 2005, 2008, 2012, 2015, 2017, 2019, 2022

**Note:** Visual Studio 2010 is NOT supported.

### SDKs

**Required (for VS before 2012):**
- DirectX SDK (version 8 or 9)

**Optional:**
- OpenSSL for TLS/SSL support
- DirectShow SDK for video
- SDL 2.0 for video rendering
- OpenH264, libyuv for video codecs

Visual Studio 2012+ includes all required SDKs.

## Build Steps

### 1. Download Source

```bash
git clone https://github.com/pjsip/pjproject.git
cd pjproject
```

### 2. Configure (Optional)

Create `pjlib/include/pj/config_site.h`:

```c
#define PJ_SCARCE_MEMORY 0

// TLS support (requires OpenSSL)
// #define PJ_HAS_SSL_SOCK 1

// Video support
// #define PJMEDIA_HAS_VIDEO 1
```

### 3. Open Solution

| Visual Studio | Solution File |
|---------------|---------------|
| 2005, 2008, 2012 | `pjproject-vs8.sln` |
| 2015, 2017, 2019, 2022 | `pjproject-vs14.sln` |

### 4. Build

1. Set `pjsua` as Startup Project
2. Select `Win32` platform
3. Choose `Debug` or `Release`
4. Build Solution (Ctrl+Shift+B)

**Output locations:**
- Applications: `pjsip-apps/bin/`
- Libraries: `lib/` under each project

## Build Configurations

| Configuration | Flags |
|---------------|-------|
| Debug | `/MTd` - static LIBC, debug |
| Release | `/MD` - dynamic MSVCRT, release |
| Debug-Static | `/MTd` |
| Debug-Dynamic | `/MDd` |
| Release-Static | `/MT` |
| Release-Dynamic | `/MD` |

## OpenSSL Setup (Optional)

1. Install OpenSSL SDK (e.g., to `C:\OpenSSL`)
2. Add `C:\OpenSSL\bin` to system PATH
3. In Visual Studio project settings:
   - Include: `C:\OpenSSL\include`
   - Library: `C:\OpenSSL\lib`
4. Link: `libeay32.lib`, `ssleay32.lib`

## Python Bindings

For Python bindings on Windows, use MinGW/MSYS2:

```bash
# In MSYS2
pacman -S swig python python-setuptools

# Build PJSIP with GNU tools
./configure CFLAGS="-fPIC"
make dep && make

# Build Python module
cd pjsip-apps/src/swig/python
make
python setup.py install
```

**Note:** Some video features (DirectShow) require Visual Studio build.

## Troubleshooting

**Missing DirectX headers:**
- Install DirectX SDK and add paths to Visual Studio

**Link errors with OpenSSL:**
- Ensure same runtime option (`/MT` vs `/MD`) for both PJSIP and OpenSSL

**Build tool version warning:**
- Edit `build/vs/pjproject-vs14-common-config.props` to set correct toolset
