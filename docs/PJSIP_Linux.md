# PJSIP Installation on Linux

## Requirements

### Tools

- GNU make
- GNU binutils
- GNU gcc

### Recommended Libraries

- ALSA (audio)
- OpenSSL, GnuTLS, or BoringSSL (TLS)

### Optional Libraries

- SDL 2.0 (video rendering)
- Video4Linux2 (video capture)
- FFMPEG, OpenH264, libyuv (video codecs)
- Opus codec

## Install Dependencies

### Ubuntu/Debian

```bash
# Essential
sudo apt-get update
sudo apt-get install build-essential git

# Audio (ALSA)
sudo apt-get install libasound2-dev

# TLS
sudo apt-get install libssl-dev

# Python bindings
sudo apt-get install swig python3-dev

# Optional: video
sudo apt-get install libsdl2-dev libv4l-dev

# Optional: codecs
sudo apt-get install libopus-dev
```

### Fedora/RHEL

```bash
# Essential
sudo dnf install gcc gcc-c++ make git

# Audio (ALSA)
sudo dnf install alsa-lib-devel

# TLS
sudo dnf install openssl-devel

# Python bindings
sudo dnf install swig python3-devel
```

### Arch Linux

```bash
sudo pacman -S base-devel git alsa-lib openssl swig python
```

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

// Video support
// #define PJMEDIA_HAS_VIDEO 1
```

### 3. Configure Build

```bash
# Basic
./configure

# With shared libraries
./configure --enable-shared

# Debug build
./configure CFLAGS="-g -O0"

# Custom install prefix
./configure --prefix=/usr/local

# View all options
./configure --help
```

### 4. Build

```bash
make dep
make
```

### 5. Install

```bash
sudo make install
```

### 6. Update Library Cache

```bash
sudo ldconfig
```

## Python Bindings

### 1. Configure with -fPIC

Create `user.mak` in pjproject root:

```makefile
export CFLAGS += -fPIC
```

Or:

```bash
./configure CFLAGS="-fPIC"
```

### 2. Build PJSIP

```bash
make dep && make
```

### 3. Build Python Module

```bash
cd pjsip-apps/src/swig/python
make
sudo make install
```

For virtualenv:

```bash
python setup.py install
```

### 4. Verify

```bash
python3 -c "import pjsua2; print('OK')"
```

## Troubleshooting

**Import error: No module named 'pjsua2':**
- Verify Python version matches build
- Add `/usr/local/lib` to `LD_LIBRARY_PATH`

**Segmentation fault:**
- Set `ep_cfg.uaConfig.threadCnt = 0` in Python code

**No audio device:**
- Install ALSA: `sudo apt-get install libasound2-dev`
- Check permissions for `/dev/snd/*`

**Library not found after install:**
- Run `sudo ldconfig`
- Add `/usr/local/lib` to `/etc/ld.so.conf.d/`

**ALSA underrun warnings:**
- Increase buffer size in audio settings
- These warnings are often harmless
