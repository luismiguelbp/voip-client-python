# PJSIP Installation on macOS

## Requirements

### Tools

- Xcode Command Line Tools
- GNU make, gcc (included with Xcode)

### Optional Libraries

- OpenSSL, GnuTLS, or BoringSSL for TLS (macOS can use native SSL)
- SDL 2.0 for video rendering
- Opus codec
- FFMPEG, OpenH264, libyuv for video

## Install Dependencies

```bash
# Install Xcode Command Line Tools
xcode-select --install

# Install Homebrew (if needed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install dependencies
brew install openssl sdl2 opus swig python3
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
// #define PJMEDIA_HAS_VID_TOOLBOX_CODEC 1  // Native H.264
```

### 3. Configure Build

```bash
# Basic
./configure

# With OpenSSL
./configure --with-ssl=/opt/homebrew/opt/openssl

# With shared libraries
./configure --enable-shared

# Debug build
./configure CFLAGS="-g -O0"

# View all options
./configure --help
```

### 4. Build

```bash
make dep
make
```

### 5. Install (Optional)

```bash
sudo make install
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
make install
```

For virtualenv:

```bash
python setup.py install
```

### 4. Verify

```bash
python3 -c "import pjsua2; print('OK')"
```

## Cross-Compilation

### x86_64 on Apple Silicon (M1/M2)

```bash
CFLAGS="-arch x86_64" LDFLAGS="-arch x86_64" \
  ./configure --host=x86_64-apple-darwin
make dep && make
```

### arm64 on Intel Mac

```bash
CFLAGS="-arch arm64" LDFLAGS="-arch arm64" \
  ./configure --host=arm-apple-darwin
make dep && make
```

## Troubleshooting

**Import error: No module named 'pjsua2':**
- Verify Python version matches build
- Check `_pjsua2.so` is in Python path

**Segmentation fault:**
- Set `ep_cfg.uaConfig.threadCnt = 0` in Python code

**Microphone not working:**
- Grant permission in System Preferences > Privacy > Microphone

**OpenSSL not found:**
- Specify path: `./configure --with-ssl=/opt/homebrew/opt/openssl`
