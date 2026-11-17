# CrossGrid

CrossGrid 将 Python 仓库里的四类证据放在一起查看：`pyproject.toml` 声明的操作系统、GitHub Actions 明确写出的测试 runner、简单的平台相关测试跳过条件，以及少量源码可移植性线索。报告回答一个有限问题：**仓库声明支持的系统是否有可见的测试 runner 证据，哪些代码假设值得维护者检查？**

CrossGrid 做静态分析，不会替你在 Windows、Linux 或 macOS 上运行项目。没有发现问题也不等于已证明可移植。

本机验证环境为 Windows：Python 3.11.0 源码测试、Python 3.14.2 隔离环境（PyYAML 6.0.3）安装后测试均通过；wheel/sdist 构建和 CLI 检查通过。GitHub Actions 的跨系统矩阵已配置但尚未实际运行；项目尚未发布到 PyPI。

English version：[README.en.md](README.en.md) · [项目规格](PROJECT_SPEC.md) · [技术机制](TECHNICAL_INNOVATION.md) · [架构](ARCHITECTURE.md)

## 安装

需要 Python 3.11+ 和 PyYAML 6.x。

```powershell
python -m pip install .
crossgrid --version
```

## 快速使用

在 Python 项目根目录执行：

```powershell
crossgrid . --html crossgrid-report.html --json crossgrid-report.json --sarif crossgrid.sarif
```

若项目没有在 `pyproject.toml` 声明支持系统，可手工指定扫描范围：

```powershell
crossgrid . --platform windows --platform linux
```

报告会分别显示项目声明和测试 runner 证据，并列出简单的 `pytest.mark.skipif` 平台条件与源码位置。

## 现有规则

- `CG101`：检测少量平台专属模块，如 `pwd`、`fcntl`、`resource`、`termios`、`winreg`。
- `CG102`：检测少量平台专属 API，如 `os.getuid()`、`os.uname()`，及选定 POSIX signal 常量。
- `CG201`：检测内置 `open`、`io.open` 或 `pathlib.Path.open` 文本模式缺少显式非空 `encoding` 的情况。
- `CG202`：提示复核 `NamedTemporaryFile` 在 Windows 上重新打开/文件锁方面的假设，置信度较低。
- `CG301`：提示复核 `os.system()` 或 subprocess `shell=True` 的 shell 差异。
- `CG801`：识别简单的平台相关 `skipif`/`skipUnless` 标记。
- `CG901`：声明支持的 OS 没有发现明确的 Actions 测试 runner。
- `CG902–905`：workflow、metadata 或源码无法分析。

规则有限且不完整。每条报告都会带有置信度、源码位置和判断依据。

## 相关工作

[CrossPortability](https://github.com/neelpatel-png/crossportability) 的公开 README 描述了对 Python 平台专属路径、API/命令、导入和环境变量的 AST 检查；[PSASpotter](https://github.com/ricardojob/PSASpotter) 及其[研究论文](https://arxiv.org/abs/2607.17462)分析平台专属 API 和防护条件。CrossGrid 的源码平台信号与这些先行工作有重叠；本项目不声称首创平台专属 API 分析。CrossGrid 把声明支持的 OS、明确写出的 GitHub Actions runner、简单测试跳过标记和选定源码信号放在一份报告中。这说明的是工作流范围，不代表已证明产品独特或用户需求成立。

## 输入与限制

符号链接的源码、元数据和 workflow 路径不会被跟随；解析后的输入必须仍位于所选项目根目录内。

- 扫描 Python 源码；跳过常见隐藏目录、生成目录和依赖目录。
- 解析 `pyproject.toml` 的 OS classifiers，包含常见 Poetry metadata。
- 读取 `.github/workflows/*.yml` 与 `.yaml` 中显式写出的 `runs-on`，以及被 `runs-on` 直接引用的标量 `strategy.matrix.os`/`include[].os`。
- 识别包含常见系统名称的简单 pytest/unittest 跳过标记。
- 输入有界：单个 Python 文件最多 8 MiB、Python 总输入最多 128 MiB、最多 20,000 个 Python 文件；单个 workflow 最多 2 MiB、workflow 总输入最多 32 MiB、最多 1,000 个 workflow；`pyproject.toml` 最多 1 MiB。超限文件会以 CG90x finding 说明，避免无界读取。
- 指定报告会以临时文件原子替换写入；目标必须互不相同，不可指向扫描输入、符号链接路径或 Git 管理目录。已有报告文件可以安全替换。

复杂/派生 YAML 表达式、超过 256 种组合或非标量维度的 matrix、可复用 workflow 的继承、GitLab/CircleCI、自定义 marker 求值、第三方依赖 wheel 兼容性和真实跨 OS 执行均不在当前范围。只有 `runs-on` 是直接的 `${{ matrix.os }}` 或 `${{ matrix['os'] }}` 时才按 matrix 计算 runner，并应用标量 exclude/include。测试任务检测使用完整单词 job/step 名称，或直接调用少数 Python 测试工具（包括 `python -m`、`py -3.x -m`、`env VAR=value`、简单 `coverage run -m`、`uv`/`poetry`/`pipenv run`）。tox/nox 只有在显式选择的环境或 session 名称含 test/unit 标记时才计入；无法解析的默认 tox/nox 运行、list/config 检查命令（包括 `tox config -e ...`）、仅 lint 选择、tox `--notest`/仅打包和 devenv 模式、help/version 和 pytest 仅收集用法均不计入。安装、echo 或仅提及工具也不计入。该命令文本启发式只能提示可能运行了测试，不能证明测试执行或通过。通用 POSIX/Unix 分类器不会被推断成 Linux 或 macOS 声明。`CG201` 识别内置 `open`、`io.open` 与常见 pathlib 打开调用；同名变量有遮蔽、重赋值或模式捕获时会禁用路径推断，任意对象的 `.open()` 不分析。这些规则均是复核线索，不是确定缺陷结论。

默认退出策略 `--fail-on error` 不会因警告或复核线索失败；输入/输出错误返回 `2`。如需更严格 CI 门禁，可选 `--fail-on warning` 或 `--fail-on review`。

## 开发与测试

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## 文档

[架构说明](ARCHITECTURE.md) · [技术机制](TECHNICAL_INNOVATION.md) · [贡献指南](CONTRIBUTING.md)

## 许可证与依赖

本项目采用 MIT 许可证，见 [LICENSE](LICENSE)。CrossGrid 依赖单独安装的 PyYAML（`>=6.0,<7`）；PyYAML 也采用 MIT 许可证，见[上游许可证说明](https://github.com/yaml/pyyaml/blob/main/README.md#license)。本项目不包含 PyYAML 源码副本。
