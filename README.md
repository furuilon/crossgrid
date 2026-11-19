# CrossGrid

[![GitHub Actions Tests](https://github.com/furuilon/crossgrid/actions/workflows/tests.yml/badge.svg?branch=main&event=push)](https://github.com/furuilon/crossgrid/actions/workflows/tests.yml)

CrossGrid 将 Python 仓库里的四类证据放在一起查看：`pyproject.toml` 或 `setup.cfg` 声明的操作系统、GitHub Actions 明确写出的测试 runner、简单的平台相关测试跳过条件，以及少量源码可移植性线索。报告回答一个有限问题：**仓库声明支持的系统是否有可见的测试 runner 证据，哪些代码假设值得维护者检查？**

CrossGrid 做静态分析，不会替你在 Windows、Linux 或 macOS 上运行项目。没有发现问题也不等于已证明可移植。

托管工作流会在 Windows、macOS、Ubuntu 上运行 Python 3.11–3.14 测试。请查看 [Actions 页面](https://github.com/furuilon/crossgrid/actions/workflows/tests.yml)核对具体提交的结果。当前独立 sdist 内容尚未单独检查，项目尚未发布到 PyPI。自动化测试不能证明扫描器对每个真实仓库都准确；生成报告也尚未经过浏览器视口检查。

当前包版本为 **0.2.0 开发快照**，尚未发布到 PyPI。SARIF 工具版本会从包版本读取。

English version：[README.en.md](README.en.md) · [项目规格](PROJECT_SPEC.md) · [技术机制](TECHNICAL_INNOVATION.md) · [架构](ARCHITECTURE.md) · [版本记录](CHANGELOG.md)

## 安装

需要 Python 3.11+ 和 PyYAML 6.x。

```powershell
python -m pip install .
crossgrid --version
```

## 快速使用

在 Python 项目根目录执行。报告写到系统临时目录，避免把生成文件留在被扫描项目中：

```powershell
$reportDir = Join-Path $env:TEMP ('crossgrid-report-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
crossgrid . `
  --html (Join-Path $reportDir 'report.html') `
  --json (Join-Path $reportDir 'report.json') `
  --sarif (Join-Path $reportDir 'report.sarif')
```

CrossGrid 优先使用静态 `[project].classifiers`。若没有该字段且未声明动态 classifiers，则读取 `[tool.poetry].classifiers`，最后回退到 setuptools `setup.cfg` 的 `[metadata] classifiers` 或旧式 `classifier`。支持多行、逗号列表、ConfigParser 插值和受限的 `file:` 引用；动态 classifiers 和构建脚本不会执行。这些来源都没有可用声明时，可手工指定扫描范围：

```powershell
crossgrid . --platform windows --platform linux
```

`setup.cfg` 与每个 classifiers 文件引用最多读取 1 MiB，引用文件合计也限制为 1 MiB，最多读取 16 个引用文件；插值展开结果最多 1 MiB，嵌套深度最多 10 层、引用最多 4,096 次，递归处理的插值文本累计最多 4 Mi 字符。引用必须是项目根目录内的普通非符号链接文件；不安全、缺失、非普通文件、格式错误或超限的引用会报告 `CG902`。缺失但位于根目录内的引用仍受报告输出路径保护，避免报告被写入后改变后续扫描结果。报告会分别显示项目声明和测试 runner 证据，并列出简单的 `pytest.mark.skipif` 平台条件与源码位置。

默认拒绝替换已有报告文件。只有检查过目标内容后才加 `--overwrite`；该选项不会绕过扫描输入、重复路径或 Git 元数据保护。

## 现有规则

- `CG101`：检测少量平台专属模块，如 `pwd`、`fcntl`、`resource`、`termios`、`winreg`。
- `CG102`：检测少量平台专属 API，如 `os.getuid()`、`os.uname()`，及选定 POSIX signal 常量。
- `CG201`：检测内置 `open`、`io.open` 或 `pathlib.Path.open` 文本模式缺少显式非空 `encoding` 的情况。
- `CG202`：提示复核 `NamedTemporaryFile` 在 Windows 上重新打开/文件锁方面的假设，置信度较低。
- `CG301`：提示复核 `os.system()` 或 subprocess `shell=True` 的 shell 差异。
- `CG801`：识别简单的平台相关 `skipif`/`skipUnless` 标记。
- `CG901`：声明支持的 OS 没有发现明确的 Actions 测试 runner。
- `CG902–905`：workflow、metadata 或源码无法分析；动态 classifiers 会标为未解析，构建脚本不会执行。
- `CG906`：runner matrix 超出分析范围或包含无法映射的 OS 标签；相关平台覆盖标为未知，不作为 `CG901` 缺少 runner 的证据。

规则有限且不完整。每条报告都会带有置信度、源码位置和判断依据。

## 相关工作

[CrossPortability](https://github.com/neelpatel-png/crossportability) 的公开 README 描述了对 Python 平台专属路径、API/命令、导入和环境变量的 AST 检查；[PSASpotter](https://github.com/ricardojob/PSASpotter) 及其[研究论文](https://arxiv.org/abs/2607.17462)分析平台专属 API 和防护条件。[PyCrossCheck 在 PyPI 上的项目页](https://pypi.org/project/pycrosscheck/)也自述提供 Windows、Linux、macOS 兼容性检查、56+ 项规则、CLI/JSON 输出和 Bandit 插件，这是直接的源码静态检查重叠。其页面中的 GitHub 源码链接仍是占位地址，当前链接返回 404，因此这里仅记录其公开描述，未验证其实现或规则覆盖范围。CrossGrid 聚焦于把声明支持的 OS 与明确写出的 GitHub Actions 测试 runner、简单测试跳过标记和有限源码线索放在同一覆盖报告里；不宣称它是唯一或更完整的兼容性检查器。

### 规则来源与整理方式

下列 Python 官方标准库文档说明所列 API 的平台限制；扫描器采用其中的有限子集，不包含外部规则数据库或 vendored linter 实现。选取的规则及匹配逻辑不代表完整的兼容性判定：[`os`](https://docs.python.org/3.11/library/os.html)、[`signal`](https://docs.python.org/3.11/library/signal.html)、[`platform`](https://docs.python.org/3.11/library/platform.html)、平台专属的 [`pwd`](https://docs.python.org/3.11/library/pwd.html)、[`grp`](https://docs.python.org/3.11/library/grp.html)、[`fcntl`](https://docs.python.org/3.11/library/fcntl.html)、[`termios`](https://docs.python.org/3.11/library/termios.html)、[`resource`](https://docs.python.org/3.11/library/resource.html)、[`pty`](https://docs.python.org/3.11/library/pty.html) 和 Windows 专属 [`winreg`](https://docs.python.org/3.11/library/winreg.html)。

`setup.cfg` 的静态读取范围参考 setuptools 的 [声明式配置文档](https://setuptools.pypa.io/en/stable/userguide/declarative_config.html)，其中说明 `[metadata] classifiers` 支持多行值、逗号列表、`classifier` 兼容别名、插值和 `file:` 引用。CrossGrid 对 `file:` 只读，不执行构建元数据代码。

## 输入与限制

符号链接的源码、元数据和 workflow 路径不会被跟随；解析后的输入必须仍位于所选项目根目录内。
CrossGrid 不是安全沙箱；它假设扫描期间没有其他进程并发替换项目文件。路径检查与文件读取/写入之间仍存在竞态窗口。

- 扫描 Python 源码；跳过常见隐藏目录、生成目录和依赖目录。
- 读取 `pyproject.toml` 的静态 OS classifiers（PEP 621 和常见 Poetry metadata）；没有静态声明时读取 setuptools `setup.cfg` 的 `[metadata] classifiers` / `classifier`，包括逗号列表、插值和受限的 `file:` 引用。动态 classifiers 不会被执行。
- 读取 `.github/workflows/*.yml` 与 `.yaml` 中显式写出的 `runs-on`，以及被 `runs-on` 直接引用的标量 `strategy.matrix.os`/`include[].os`。
- 识别包含常见系统名称的简单 pytest/unittest 跳过标记。
- 输入有界：单个 Python 文件最多 8 MiB、Python 总输入最多 128 MiB、最多 20,000 个 Python 文件；单个 workflow 最多 2 MiB、workflow 总输入最多 32 MiB、最多 1,000 个 workflow；`pyproject.toml`、`setup.cfg` 和每个 `file:` classifier 文件最多 1 MiB，classifier 插值展开最多 1 MiB/10 层/4,096 次引用，递归处理文本最多 4 Mi 字符，引用文件合计最多 1 MiB/16 个。超限文件会以 CG90x finding 说明，避免无界读取。
- 指定报告目标必须互不相同，且不能覆盖扫描输入、符号链接或 Windows junction/reparse-point 路径，或 Git 管理目录。默认拒绝已有文件；只有确认目标后才使用 `--overwrite`。新文件以不覆盖方式创建；替换现有文件时使用同目录临时文件和原子替换。若文件系统不支持硬链接，会使用独占创建后复制；复制被中断或失败时可能留下不完整的新报告，但不会替换已有目标。

### Workflow 与 runner 范围

- 不解析复杂或派生 YAML 表达式、可复用 workflow 的继承、GitLab/CircleCI、自定义 marker 求值、第三方依赖 wheel 兼容性，也不会在其他 OS 上实际运行项目。
- 只有 `runs-on` 直接使用 `${{ matrix.os }}` 或 `${{ matrix['os'] }}` 时才计算 matrix runner；支持标量 `exclude`/`include`。超过 256 种组合、含非标量维度或无法映射 OS 标签的 matrix 会产生 `CG906`，并将相关平台标为“未知”；不会把分析器上限或未知 runner 误报成确定的缺少 runner (`CG901`)。
- 测试任务的判断依赖完整单词 job/step 名称，或可识别的直接测试命令，例如 `python -m`、`py -3.x -m`、`env VAR=value`、简单的 `coverage run -m`、`uv`/`poetry`/`pipenv run`。`tox`/`nox` 只有在显式选择的环境或 session 名称带有 `test`/`unit` 标记时才计入。
- 无法解析的默认 `tox`/`nox`、list/config 检查（包括 `tox config -e ...`）、仅 lint、`tox --notest`/仅打包、devenv、help/version、pytest 仅收集、安装、echo 和仅提及测试工具都不计入。命令文本只能提示可能执行了测试，不能证明执行或通过。
- 通用 POSIX/Unix classifier 不会自动推断成 Linux 或 macOS 支持声明。

### 源码规则的边界

- `CG201` 识别内置 `open`、`io.open` 和常见 `pathlib.Path.open` 调用。出现同名遮蔽、重赋值或结构化模式捕获时，路径变量推断会关闭；不分析任意对象的 `.open()` 方法。
- `CG201`、`CG202` 和 `CG301` 是复核提示，不是确定缺陷结论；规则可能漏报或误报。

默认退出策略 `--fail-on error` 不会因警告或复核线索失败；输入/输出错误返回 `2`。如需更严格 CI 门禁，可选 `--fail-on warning` 或 `--fail-on review`。

## 开发与测试

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## 文档

[架构说明](ARCHITECTURE.md) · [技术机制](TECHNICAL_INNOVATION.md) · [贡献指南](CONTRIBUTING.md)

## 许可证与依赖

本项目采用 MIT 许可证，见 [LICENSE](LICENSE)。CrossGrid 依赖单独安装的 PyYAML（`>=6.0,<7`）；检查环境使用的 PyYAML 6.0.3 采用 MIT 许可证，见[该版本的上游许可证文件](https://github.com/yaml/pyyaml/blob/6.0.3/LICENSE)。本项目不包含 PyYAML 源码副本。
