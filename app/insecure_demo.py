import hashlib
import subprocess


def run(cmd):
    subprocess.call(cmd, shell=True)


def fingerprint(text):
    return hashlib.md5(text.encode()).hexdigest()
