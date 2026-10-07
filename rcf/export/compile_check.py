"""Compile an exported Unity controller against real UnityEngine assemblies with `dotnet build`.

This checks that every API used exists with the right signature in the installed Unity
versions (no Unity Editor licence needed). The legacy-input branch is compiled; the
Input System branch is verified with a Unity batch-mode compile (tools/unity_batch_check.py).
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MODULES = ["UnityEngine.CoreModule.dll", "UnityEngine.Physics2DModule.dll", "UnityEngine.InputLegacyModule.dll",
           "UnityEngine.JSONSerializeModule.dll"]


@dataclass
class CompileResult:
    unity: str
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    output: str = ""


def find_unity_editors() -> list[Path]:
    roots = [os.environ.get("RCF_UNITY_EDITORS", ""), r"C:\Program Files\Unity\Hub\Editor",
             "/Applications/Unity/Hub/Editor", str(Path.home() / "Unity/Hub/Editor")]
    out = []
    for r in roots:
        if r and Path(r).is_dir():
            for d in sorted(Path(r).iterdir()):
                managed = d / "Editor" / "Data" / "Managed" / "UnityEngine"
                if (managed / "UnityEngine.CoreModule.dll").exists():
                    out.append(d)
    return out


def dotnet_available() -> bool:
    return shutil.which("dotnet") is not None


def compile_with_unity(cs_file: Path, editor: Path, defines: str = "") -> CompileResult:
    managed = editor / "Editor" / "Data" / "Managed" / "UnityEngine"
    refs = "\n".join(f'    <Reference Include="{m[:-4]}"><HintPath>{managed / m}</HintPath><Private>false</Private></Reference>'
                     for m in MODULES if (managed / m).exists())
    proj = f"""<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>netstandard2.1</TargetFramework>
    <LangVersion>9.0</LangVersion>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
    <DefineConstants>{defines}</DefineConstants>
    <NoWarn>CS0414</NoWarn>
  </PropertyGroup>
  <ItemGroup>
    <Compile Include="{cs_file.resolve()}" />
{refs}
  </ItemGroup>
</Project>
"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "UnityCompileCheck.csproj"
        p.write_text(proj)
        env = dict(os.environ, DOTNET_CLI_UI_LANGUAGE="en", DOTNET_NOLOGO="1", DOTNET_CLI_TELEMETRY_OPTOUT="1")
        r = subprocess.run(["dotnet", "build", str(p), "-nologo", "-v", "q", "-clp:NoSummary"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, env=env)
    out = r.stdout + r.stderr
    errors = sorted(set(re.findall(r"error (CS\d+: [^\[\r\n]+)", out)))
    warnings = sorted(set(re.findall(r"warning (CS\d+: [^\[\r\n]+)", out)))
    return CompileResult(editor.name, r.returncode == 0 and not errors, errors, warnings, out)


def check_all(cs_file: Path) -> list[CompileResult]:
    return [compile_with_unity(cs_file, e) for e in find_unity_editors()]


if __name__ == "__main__":
    import sys
    for res in check_all(Path(sys.argv[1])):
        print(res.unity, "OK" if res.ok else "FAILED")
        for e in res.errors:
            print("  error", e)
        for w in res.warnings:
            print("  warning", w)
