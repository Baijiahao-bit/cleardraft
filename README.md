# 净稿 · ClearDraft

**Word / PDF 文档清理与重建工具。** 在本机检查、清理和重新导出文档，并生成可复核的审计报告。

[项目主页](https://github.com/Baijiahao-bit/cleardraft) · [下载 ZIP](https://github.com/Baijiahao-bit/cleardraft/archive/refs/heads/main.zip) · [使用方法](docs/使用方法.md)

**本工具能检测或保证消除 OpenAI textGrain 隐形文本水印。把相同文字重新输入、复制或导出，去除统计选词水印的方法

审计报告中的官方水印检测状态为 `not_tested`。本项目没有接入官方检测器，没有据此验证真实用户文件；演示证据只能证明本地检查项目的前后变化，不能证明“GPT 水印已去除”或“AI 检测通过”。

## 能做什么

| 输入 | 保留模式 `preserve`（默认） | 重建模式 `rebuild` |
| --- | --- | --- |
| `.docx` | 保留文档结构，清理指定文本字符、部分文档属性及编辑会话标识 | 抽取正文，建立新文档并重新排版 |
| `.pdf` | 保留页面内容，清理支持的元数据；不修改页面中的文字 | 抽取文本、清理指定字符并重新排版 |
| UTF-8 `.txt`、`.md` | 清理指定字符，导出文本 | 可将抽取文本导出为其他支持格式 |

支持 `.docx`、`.pdf`、`.txt`、`.md` 输出。跨格式转换使用重建模式。旧版 `.doc` 请先另存为 `.docx`。扫描 PDF 的图片文字不支持 OCR：遇到无法抽取文字的扫描页会拒绝重建。

指定清理字符仅为 `U+200B`、`U+2060`、`U+FEFF`、`U+00AD`。这些字符也可能有正常排版用途；其他格式字符（如 ZWJ、ZWNJ、变体选择符）不会被一概删除。可用 `--keep-invisibles` 保留上述四种字符。

DOCX 的图片、公式、批注、页眉页脚等复杂内容可能在重建中丢失。工具对识别到的复杂内容默认拒绝重建，只有显式启用 `--allow-lossy` 才继续。保留模式也不承诺删除所有嵌入信息，报告会列出可检查到的候选项目。这里的文本清理不等于清除可见图片水印。

## Windows 快速开始

在仓库主页点击 **Code → Download ZIP**，解压完整项目后按下面步骤运行。请勿直接在压缩包中双击脚本。

1. 安装 Python 3.11 或更高版本，安装时启用 **Add python.exe to PATH**。
2. 将本项目完整解压到一个文件夹，双击 `安装.cmd` 安装依赖。
3. 双击 `启动.cmd` 打开本地图形界面，选择文件，先检查，再选择输出模式和新文件名。
4. 打开输出文档，并查看同次处理生成的 JSON 审计报告。

首次安装依赖需要联网；文档检查与处理在本机完成。输入原件和已有输出文件不会被覆盖；请为每次运行选择新输出路径。

完整步骤、模式选择与常见错误见 [使用方法](docs/使用方法.md)。

## 命令行

在项目目录打开终端：

```powershell
python -m pip install -r requirements.txt
python -m cleanroom --help
python -m cleanroom gui
python -m cleanroom inspect "input.docx"
python -m cleanroom clean "input.docx" -o "output.docx"
python -m cleanroom clean "input.pdf" -o "output.pdf" --mode preserve
python -m cleanroom clean "input.docx" -o "rebuilt.pdf" --mode rebuild
```

最后一条命令将重新排版。若报告存在复杂内容，先阅读风险提示，确认接受丢失后再显式增加 `--allow-lossy`。该选项不会提供 OCR，也不是统计水印处理选项。输出为 `output.docx` 时，审计报告为 `output.docx.audit.json`。

## 如何理解证据

报告包括本地检查结果、处理前后文件信息、文本一致性结果和检测限制。正文比较以选定清理规则处理后的预期文本为基准，忽略全部空白，另外列出精确比较结果；它不证明空格、分页、公式、图片或版式完全一致。

`evidence/sample-run/` 保存交付的合成样例及运行记录。独立核对现有证据，或重新生成一套证据：

```powershell
python scripts/verify_evidence.py evidence/sample-run
python scripts/generate_evidence.py --output evidence/new-run
python scripts/verify_evidence.py evidence/new-run
```

新一轮证据请使用新的输出目录。独立验证脚本直接读取 DOCX 内部 XML 和 PDF 对象，不调用本工具的扫描、清理函数；它核对合成样例的指定字符、所检属性、连接字符保留及 PDF 页面内容流。

“指定字符剩余数量为零”“所检元数据已清空”和“textGrain 检测阴性”是不同结论。前两项可本地验证，第三项在本项目中始终是 **未测试**。详见 [验证说明](docs/验证说明.md) 和 [技术原理与官方依据](docs/技术原理与官方依据.md)。

## 开发与上传 GitHub

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest --cov=cleanroom --cov-report=term-missing --cov-fail-under=80
```

项目提供 Windows / Ubuntu 与 Python 3.11 / 3.12 / 3.13 的 GitHub Actions 测试配置。配置存在并不代表托管 CI 已运行通过，发布前以自己仓库中的实际结果为准。

上传时包括源码、测试、说明文档、依赖文件、启动脚本及合成样例证据；不要上传个人文档、`.venv` 或本机缓存。建议仓库简介写为“Local document cleanup and auditable reconstruction”，避免宣称已经实现 textGrain 去除。

## English summary

ClearDraft (净稿) inspects and cleans selected invisible characters and document metadata locally, and can rebuild DOCX, PDF, and UTF-8 text documents. Rebuilding can lose layout and complex content. It does **not** detect or guarantee removal of OpenAI textGrain or other statistical text watermarks. Retyping the same words is not evidence of removal. Official watermark detection is reported as `not_tested`; local cleanup checks and synthetic examples must not be presented as an official watermark test.

MIT license. See [LICENSE](LICENSE).
