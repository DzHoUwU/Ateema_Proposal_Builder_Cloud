import pkg_resources

packages = [
    "streamlit",
    "pandas",
    "numpy",
    "langchain-core",
    "python-dateutil",
    "pyarrow",
    "faiss-cpu",
    "typing-extensions",
]

for pkg in packages:
    try:
        version = pkg_resources.get_distribution(pkg).version
        print(f"{pkg} == {version}")
    except Exception:
        print(f"{pkg} not installed")
