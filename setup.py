from setuptools import setup, find_packages

setup(
    name="data_service",
    version="0.1.0",
    description="Hyperliquid DEX Trading System - Data Service Module",
    author="QuantMuse",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        line.strip()
        for line in open("requirements.txt").readlines()
        if line.strip() and not line.startswith("#")
    ],
)
