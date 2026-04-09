import re
p=open("browser_ops_injector.py","r",encoding="utf-8").read()
start = p.find("function patchHeroOperationTitle")
chunk = p[start:start+2500]
for line in chunk.splitlines():
    if ".replace(/" in line and "/g" in line and "00A0" not in line and "s+" not in line:
        inside = line.split(".replace(/")[1].split("/g")[0]
        repl = line.split("/g, ")[1].split(")")[0].strip().strip("'").strip('"')
        print("pattern chars:", [hex(ord(c)) for c in inside])
        print("repl chars:", [hex(ord(c)) for c in repl])
