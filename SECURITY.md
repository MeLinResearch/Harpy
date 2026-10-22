# TLS trust-store update, 2026-10-22

The eMudhra/emSign CA key compromise can permit forged HTTPS certificates when a client still trusts the affected authorities. It does not by itself establish that this repository or its API credentials were compromised.

Follow the [Python security guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576): use certifi >=2026.10.19, pip >=26.2.2, and uv >=0.13.4 wherever applicable. Update the operating system's CA package too. A browser update does not patch Python environments or containers.

For each existing virtual environment, activate it and bootstrap pip using the official wheel and SHA-256 from that guidance before downloading other packages:

```bash
python -m pip install --no-cache-dir "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
python -m pip install --upgrade "certifi>=2026.10.19"
python -m pip --version
python -c "import certifi; from pip._vendor import certifi as p; print('application certifi:', certifi.__version__); print('pip certifi:', p.__version__)"
```

Use a supported Python environment, at least Python 3.10 for this pip release. Repeat after creating a new environment if its bundled pip is older. Upgrade any separate tool installations and any application-specific/custom CA bundles as well. Never disable certificate verification to work around errors.

HARPY has no certifi runtime dependency. Its CI pins patched uv 0.13.5. Upgrade local uv with the patched pip above:

```bash
python -m pip install --upgrade "uv>=0.13.4"
uv --version
```
If uv was installed another way, update that installation and check the executable on your PATH.
