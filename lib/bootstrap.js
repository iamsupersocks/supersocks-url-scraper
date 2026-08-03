'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { fail } = require('./errors');
const { readPackageVersion, resolveCacheRoot, versionPaths, acquireLock } = require('./cache');
const { resolvePython } = require('./python');

function packageRootFrom(moduleDir = path.join(__dirname, '..')) {
  return path.resolve(moduleDir);
}

function assertEmbeddedPackage(packageRoot, { existsImpl = fs.existsSync } = {}) {
  const pyproject = path.join(packageRoot, 'pyproject.toml');
  const src = path.join(packageRoot, 'src', 'supersocks_url_scraper');
  if (!existsImpl(pyproject) || !existsImpl(src)) {
    fail(
      `Embedded Python package missing under ${packageRoot}. Reinstall the npm package (src/ and pyproject.toml are required).`,
    );
  }
}

function venvPythonPath(venvDir) {
  if (process.platform === 'win32') {
    return path.join(venvDir, 'Scripts', 'python.exe');
  }
  return path.join(venvDir, 'bin', 'python');
}

function venvCliPath(venvDir) {
  if (process.platform === 'win32') {
    return path.join(venvDir, 'Scripts', 'supersocks-url-scraper.exe');
  }
  return path.join(venvDir, 'bin', 'supersocks-url-scraper');
}

function readMarker(paths, { existsImpl = fs.existsSync, readFileImpl = fs.readFileSync } = {}) {
  if (!existsImpl(paths.readyMarker)) {
    return null;
  }
  try {
    const lines = String(readFileImpl(paths.readyMarker, 'utf8'))
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    if (!lines.length) {
      return null;
    }
    return { version: lines[0], packageRoot: lines[1] || '' };
  } catch {
    return null;
  }
}

function isReady(paths, packageRoot, { existsImpl = fs.existsSync, readFileImpl = fs.readFileSync } = {}) {
  if (!existsImpl(venvPythonPath(paths.venvDir))) {
    return false;
  }
  const marker = readMarker(paths, { existsImpl, readFileImpl });
  if (!marker) {
    return false;
  }
  return marker.version === readPackageVersion(packageRoot) && path.resolve(marker.packageRoot) === path.resolve(packageRoot);
}

function runChecked(command, args, { spawnSyncImpl = spawnSync, cwd, env = process.env, label } = {}) {
  const result = spawnSyncImpl(command, args, {
    cwd,
    env,
    encoding: 'utf8',
  });
  if (result.error) {
    fail(`${label || command} failed to start: ${result.error.message}`);
  }
  if (result.status !== 0) {
    const detail = [result.stderr, result.stdout].filter(Boolean).join('\n').trim();
    fail(`${label || command} failed (exit ${result.status})${detail ? `: ${detail.split('\n')[0]}` : ''}`);
  }
  return result;
}

/**
 * Install the embedded package into the venv without network access.
 * Copies src into site-packages and writes a console-script wrapper.
 * Avoids hatchling/build isolation so first run stays offline and local.
 */
function installEmbeddedOffline({
  pythonCommand,
  stagingDir,
  packageRoot,
  version,
  finalPaths,
  spawnSyncImpl = spawnSync,
  mkdirImpl = fs.mkdirSync,
  rmImpl = fs.rmSync,
  renameImpl = fs.renameSync,
  writeFileImpl = fs.writeFileSync,
  existsImpl = fs.existsSync,
}) {
  rmImpl(stagingDir, { recursive: true, force: true });
  mkdirImpl(stagingDir, { recursive: true });
  const stagingVenv = path.join(stagingDir, 'venv');

  runChecked(pythonCommand, ['-m', 'venv', stagingVenv], {
    spawnSyncImpl,
    label: 'python -m venv',
  });

  const stagingPython = venvPythonPath(stagingVenv);
  const installer = `
import pathlib, shutil, sys, os, compileall

package_root = pathlib.Path(sys.argv[1]).resolve()
version = sys.argv[2]
src_pkg = package_root / "src" / "supersocks_url_scraper"
if not src_pkg.is_dir():
    raise SystemExit(f"missing embedded package at {src_pkg}")

# Prefer the venv purelib path.
candidates = []
if hasattr(sys, "base_prefix"):
    pass
try:
    import site
    candidates.extend(site.getsitepackages())
except Exception:
    pass
try:
    import sysconfig
    purelib = sysconfig.get_paths().get("purelib")
    if purelib:
        candidates.insert(0, purelib)
except Exception:
    pass

site_dir = None
for item in candidates:
    path = pathlib.Path(item)
    if path.exists():
        site_dir = path
        break
if site_dir is None:
    raise SystemExit("could not resolve venv site-packages")

dest = site_dir / "supersocks_url_scraper"
if dest.exists():
    shutil.rmtree(dest)
shutil.copytree(src_pkg, dest)
compileall.compile_dir(str(dest), quiet=1)

dist_info = site_dir / f"supersocks_url_scraper-{version}.dist-info"
if dist_info.exists():
    shutil.rmtree(dist_info)
dist_info.mkdir(parents=True)
(dist_info / "METADATA").write_text(
    "Metadata-Version: 2.1\\n"
    "Name: supersocks-url-scraper\\n"
    f"Version: {version}\\n"
    "Summary: Embedded offline install from npm package\\n",
    encoding="utf-8",
)
(dist_info / "INSTALLER").write_text("supersocks-url-scraper-npm\\n", encoding="utf-8")
(dist_info / "RECORD").write_text("", encoding="utf-8")

bin_dir = pathlib.Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
bin_dir.mkdir(parents=True, exist_ok=True)
# Placeholder script; shebang rewritten after atomic rename to the final venv path.
script_name = "supersocks-url-scraper.exe" if os.name == "nt" else "supersocks-url-scraper"
script_path = bin_dir / script_name
script_path.write_text(
    "#!/usr/bin/env python3\\n"
    "from supersocks_url_scraper.cli import main\\n"
    "if __name__ == \\"__main__\\":\\n"
    "    raise SystemExit(main())\\n",
    encoding="utf-8",
    newline="\\n",
)
if os.name != "nt":
    script_path.chmod(0o755)
print(str(script_path))
`.trim();

  runChecked(stagingPython, ['-c', installer, packageRoot, version], {
    spawnSyncImpl,
    label: 'offline embedded install',
  });

  writeFileImpl(path.join(stagingDir, '.ready'), `${version}\n${path.resolve(packageRoot)}\n`, 'utf8');

  mkdirImpl(path.dirname(finalPaths.versionRoot), { recursive: true });
  rmImpl(finalPaths.versionRoot, { recursive: true, force: true });
  renameImpl(stagingDir, finalPaths.versionRoot);

  const finalPython = venvPythonPath(finalPaths.venvDir);
  const finalCli = venvCliPath(finalPaths.venvDir);
  if (!existsImpl(finalPython)) {
    fail(`Install completed but venv is missing under ${finalPaths.venvDir}`);
  }
  // Rewrite console script with the final interpreter path (staging shebang would be stale after rename).
  writeFileImpl(
    finalCli,
    `#!${finalPython}\nfrom supersocks_url_scraper.cli import main\nif __name__ == "__main__":\n    raise SystemExit(main())\n`,
    { encoding: 'utf8', mode: 0o755 },
  );
}

function ensureRuntime({
  packageRoot = packageRootFrom(),
  env = process.env,
  homedir,
  spawnSyncImpl = spawnSync,
  resolvePythonImpl = resolvePython,
  acquireLockImpl = acquireLock,
  existsImpl = fs.existsSync,
  readFileImpl = fs.readFileSync,
  mkdirImpl = fs.mkdirSync,
  rmImpl = fs.rmSync,
  renameImpl = fs.renameSync,
  writeFileImpl = fs.writeFileSync,
  sleepImpl,
} = {}) {
  assertEmbeddedPackage(packageRoot, { existsImpl });
  const version = readPackageVersion(packageRoot);
  const cacheRoot = resolveCacheRoot(env, homedir);
  mkdirImpl(cacheRoot, { recursive: true });
  const paths = versionPaths(cacheRoot, version);

  if (isReady(paths, packageRoot, { existsImpl, readFileImpl })) {
    return {
      version,
      cacheRoot,
      packageRoot: path.resolve(packageRoot),
      ...paths,
      python: venvPythonPath(paths.venvDir),
      cli: venvCliPath(paths.venvDir),
      created: false,
    };
  }

  const release = acquireLockImpl(paths.lockDir, {
    mkdirImpl,
    rmImpl,
    existsImpl,
    sleepImpl,
  });

  try {
    if (isReady(paths, packageRoot, { existsImpl, readFileImpl })) {
      return {
        version,
        cacheRoot,
        packageRoot: path.resolve(packageRoot),
        ...paths,
        python: venvPythonPath(paths.venvDir),
        cli: venvCliPath(paths.venvDir),
        created: false,
      };
    }

    const python = resolvePythonImpl({ env, spawnSyncImpl });
    installEmbeddedOffline({
      pythonCommand: python.command,
      stagingDir: paths.stagingDir,
      packageRoot,
      version,
      finalPaths: paths,
      spawnSyncImpl,
      mkdirImpl,
      rmImpl,
      renameImpl,
      writeFileImpl,
      existsImpl,
    });

    return {
      version,
      cacheRoot,
      packageRoot: path.resolve(packageRoot),
      ...paths,
      python: venvPythonPath(paths.venvDir),
      cli: venvCliPath(paths.venvDir),
      created: true,
    };
  } finally {
    release();
  }
}

module.exports = {
  packageRootFrom,
  assertEmbeddedPackage,
  venvPythonPath,
  venvCliPath,
  isReady,
  ensureRuntime,
  installEmbeddedOffline,
};
