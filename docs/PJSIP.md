# PJSIP Overview

PJSIP is an open-source multimedia communication library for SIP, media, and NAT traversal. PJSUA2 is the high-level C++ API with Python bindings for building VoIP applications.

**Latest Version:** 2.16 (Released November 26, 2025)

## Platform Guides

- [Windows Installation](PJSIP_Windows.md)
- [macOS Installation](PJSIP_macOS.md)
- [Linux Installation](PJSIP_Linux.md)

## Download

```bash
# Clone from GitHub
git clone https://github.com/pjsip/pjproject.git
cd pjproject

# Or download a specific release
wget https://github.com/pjsip/pjproject/archive/refs/tags/2.16.tar.gz
tar -xzf 2.16.tar.gz
```

**Official Sources:**
- GitHub: https://github.com/pjsip/pjproject
- Website: https://www.pjsip.org/download.htm

## Python Bindings (PJSUA2)

PJSUA2 provides SWIG-generated Python bindings for building VoIP applications.

### Building Python Module

1. **Configure with `-fPIC`** (create `user.mak` in pjproject root):

   ```makefile
   export CFLAGS += -fPIC
   ```

2. **Build PJSIP:**

   ```bash
   make dep && make
   ```

3. **Build Python module:**

   ```bash
   cd pjsip-apps/src/swig/python
   make
   make install  # or: python setup.py install (for virtualenv)
   ```

4. **Verify:**

   ```python
   python3 -c "import pjsua2; print('OK')"
   ```

## Basic Usage

```python
import pjsua2 as pj

# Create and initialize endpoint
ep = pj.Endpoint()
ep_cfg = pj.EpConfig()
ep_cfg.uaConfig.threadCnt = 0  # Required for Python

ep.libCreate()
ep.libInit(ep_cfg)

# Create UDP transport
transport_cfg = pj.TransportConfig()
transport_cfg.port = 5060
ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, transport_cfg)

ep.libStart()

# Create account
acc_cfg = pj.AccountConfig()
acc_cfg.idUri = "sip:user@domain.com"
acc_cfg.regConfig.registrarUri = "sip:domain.com"

cred = pj.AuthCredInfo()
cred.scheme = "digest"
cred.realm = "*"
cred.username = "user"
cred.dataType = 0
cred.data = "password"
acc_cfg.sipConfig.authCreds.append(cred)

class MyAccount(pj.Account):
    def onRegState(self, prm):
        print(f"Registration: {prm.code} {prm.reason}")

acc = MyAccount()
acc.create(acc_cfg)

# Run event loop
import time
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass

ep.libDestroy()
```

## Sample Applications

| Sample | Description |
|--------|-------------|
| [pjsua2_demo.cpp](https://github.com/pjsip/pjproject/blob/master/pjsip-apps/src/samples/pjsua2_demo.cpp) | C++ basic usage |
| [pygui](https://github.com/pjsip/pjproject/tree/master/pjsip-apps/src/pygui) | Python GUI for calls |
| [confbot](https://github.com/pjsip/pjproject/tree/master/pjsip-apps/src/confbot) | Python conference server |

## Resources

- **Documentation:** https://docs.pjsip.org/
- **GitHub:** https://github.com/pjsip/pjproject
- **Issues:** https://github.com/pjsip/pjproject/issues
