# 广告图片本地化 · 代码还原版

**用 coding agent 给广告图做多语言、多尺寸本地化，不需要生图模型。**

[English](./README.md) · [SKILL.md](./skills/ad-image-localization/SKILL.md) · [完整示例](./skills/ad-image-localization/examples/rabbit-social-networks/) · [GPT Image 版](https://github.com/kouzt123/ad-image-localization-codex)

这是 [ad-image-localization-codex](https://github.com/kouzt123/ad-image-localization-codex) 的姊妹项目。GPT Image 版的做法是让模型把每种语言、每个尺寸重新画一遍。这个版本面向**不能生图**的 coding agent，完全依靠它们擅长的事情：写代码。agent 先把广告图拆成图层，再用 HTML/CSS 还原成响应式页面（文字是真实可编辑的），最后用无头 Chrome 渲染出所有语言 × 所有尺寸。

**先说清楚：效果不如 GPT Image。** GPT 版会为每个画幅重新构思构图；这个版本只能把原图的元素重新排列。不过在这个限制下效果已经不错了，而且有几件事做得比 GPT 版好：文字永远正确、极端横幅尺寸、自动 QA、秒级重渲染。下面是如实的对比。

![GPT Image 与代码还原：四种语言的 1200x628](./docs/images/compare-1200x628.jpg)

*左：GPT Image 版；右：本仓库。同一个 campaign、同一个目标尺寸。代码版唯一的输入是 GPT 版产出的 1200x1200 英文图。*

---

## 相比 GPT Image 版的短板

以下问题都能在上面和下面的兔子示例里看到。

1. **不会为每个画幅重新构图。** GPT Image 会针对每个比例重画画面：9:16 里兔子是全身、标题大得多；1200x628 里标题排成三行大字。代码版沿用原图构图，只做移动、缩放和取舍。能用，但竖版和横版更像套模板，主体也会被裁掉（9:16 里兔子的腿被切掉了）。
2. **主体是"冻住"的。** 抠出来的主体只能移动和缩放，不能换姿势、转身或补全。原图里被裁掉的部分（一条腿、产品的边缘）画不回来。
3. **主体移走后露出的背景。** 原来被主体挡住的区域由 LaMa 补全，比其他地方模糊一些。新增的画布区域用原图真实纹理拼接（见下文"背景"），但纹理拼接可能让显眼的物体重复出现，比如同一朵大雏菊出现两次。
4. **重建的 UI 是平的。** 手机界面用 HTML 重建，这样文字才能翻译、才能做 RTL，但丢掉了 3D 渲染的光影和立体感。GPT 版还会**连界面里的内容一起本地化**：日文人名、换一张帖子配图、阿拉伯语用户。代码版只换文字，图片不变。
5. **字形风格。** GPT 会模仿原图的字体风格重新写字；代码版用最接近的 Google Font。印在曲面或 3D 表面上的字（包装、衣服）只能平面修补，或者保留原文。
6. **不能新增图像内容。** GPT 会加一些本地化的小元素，比如额外的贴纸、带文化特色的细节。代码只能画光效和矢量类元素：雾、光晕、闪光、渐变、扁平图形。我们试过把整张手绘风格的背景用代码画出来，放在 3D 渲染的角色旁边像剪贴画，风格不搭。
7. **每张新素材都要 agent 花工夫。** 第一次还原一张新素材需要 agent 工作几十分钟：测量、写 `scene.json`、写 HTML、反复看渲染结果修改。最终质量取决于 agent 的设计判断。GPT Image 只需要每张图一句提示词。
8. **本地依赖较重。** Python 依赖约 1.5 GB（torch、onnxruntime），模型约 380 MB（rembg、LaMa），还需要 Google Chrome。

## 比 GPT Image 版强的地方

- **文字永远正确。** 用真实字体排版：不会拼错，不会糊字，阿拉伯语连写正确，日语按词组断行。（GPT 版自己的 README 也写了：生图"仍可能扭曲小字、logo、手、脸和密集 UI"。）
- **品牌资产像素级一致。** logo、角色、产品都是从原图抠出来的，不是重画的。
- **极端尺寸精确、可复现。** 两个版本都会认真对待 `320x50`、`728x90`、`160x600` 这类尺寸：GPT 版会先选最接近的安全比例、做确定性裁切，裁切会伤到构图时，再让模型重新扩图或重新排版，而且重排更有创意。代码版的优势在于精确：每个尺寸都是明确的排版规则，即使只有 50px 高，文字依然清晰、拼写正确，每种语言的渲染结果也完全一致。
- **结果确定、可重复渲染。** `creative.html` + `copy/<语言>.json` 就是可编辑的母版：加一种语言只需要加一个 JSON 文件，渲染 48 张大约 1 分钟。
- **每次渲染自动 QA：** 文字溢出、超出画布、字号过小、互相重叠、压到人脸或产品（按抠图透明度判断）、Story 安全区，以及**按文字背后实际像素计算的 WCAG 对比度**。之后还有记录在案的人工视觉审核和 release gate。
- **不需要生图模型或 API。** 全部本地运行。

![竖版：GPT 与代码版对比](./docs/images/compare-tall.jpg)

![IAB 极端尺寸](./docs/images/extreme-sizes.jpg)

---

## 原理

![流程](./docs/images/how-it-works.jpg)

1. **识别：** agent 查看原图，在带坐标的网格上测量每个元素（`decompose.py grid`、`crop`，倾斜的 UI 用 `rotate` 摆正）。
2. **拆层：** 写好 `scene.json` 后运行 `decompose.py decompose`，得到透明抠图（rembg 或按颜色抠图）、去掉文字和主体的干净底图（有纹理的区域用 LaMa，平滑的天空和渐变用 push-pull），以及 `layers.json`。
3. **每个尺寸一张原生背景：** `decompose.py extend` 为每个交付尺寸生成一张同尺寸底图。比原图高的尺寸，天空按一阶连续（C1）的渐变模型向上延伸；比原图宽的尺寸，用原图**未被遮挡**的真实像素横向拼接地面；极细的横幅直接裁切。光效层（原设计里的柔光面板、光晕、闪光）用 `scene-kit.js` 按每种版式在代码里绘制。扁平、矢量、渐变风格的源图可以整张背景都用代码画。
4. **还原成页面：** agent 编写 `creative.html`：7 种比例版式（strip、billboard、landscape、square、portrait、story、skyscraper），用 CSS 逻辑属性让 RTL 自动镜像，文字自动缩放适配，UI 界面用 HTML 重建。
5. **渲染 + QA：** `render.py` 用无头 Chrome 渲染所有语言 × 尺寸，同时收集 DOM QA 结果；agent 再逐一查看每种语言的 contact sheet。最后是 Culture-Aware QA、manifest 和 `release-check`。

| 源图背景 | 做法 |
|---|---|
| 纯色、渐变、矢量、光斑 | 整张用代码重画（`scene-kit.js`），任意尺寸都像素级清晰 |
| 手绘或照片质感的地面 + 天空（兔子图属于这类） | `extend`：拼接真实纹理，按模型延伸天空，再叠加代码绘制的光效 |
| 结构化场景（房间、街道） | 底图 + 裁切；想要最好效果请提供 PSD / Figma 分层源文件 |

*上：一张底图拉伸到 16:9；下：原生 1920x1080 底图，用真实纹理拼接，并用代码重画原设计里的柔光面板。*

![原生底图前后对比](./docs/images/plate-before-after.jpg)

*阿拉伯语（RTL）全部 12 个尺寸：*

![阿拉伯语 contact sheet](./docs/images/contact-sheet-ar.jpg)

---

## 适用于各种有能力的 coding agent

这个 skill 使用 [Agent Skills](https://agentskills.io) 的 `SKILL.md` 格式，繁重的工作都由普通的 Python 和 HTML 完成。**要求：** agent 能执行 shell 命令、能编辑文件、能**看图**（视觉审核是必做步骤）；系统为 macOS 或 Linux；Python 3.10+（推荐用 `uv`）；Google Chrome（或 Playwright 自带的 Chromium）。

**Claude Code**

```text
/plugin marketplace add kouzt123/ad-image-localization
/plugin install ad-image-localization@kouzt123-ad-image-localization
```

**Codex**

```bash
codex plugin marketplace add kouzt123/ad-image-localization
codex plugin add ad-image-localization@kouzt123-ad-image-localization
```

**其他支持 `SKILL.md` 的 agent：** 把 `skills/ad-image-localization/` 复制或软链接到该 agent 的 skills 目录。

**不支持 skill 的 agent：** 直接打开这个仓库，根目录的 `AGENTS.md` 会引导 agent 去读 `SKILL.md`；或者直接告诉 agent："读 skills/ad-image-localization/SKILL.md 并照着做"。

装好后执行一次：

```bash
bash skills/ad-image-localization/scripts/setup.sh    # 创建 skill 自带的 .venv
```

## 使用

> 把这张图本地化成德语、日语、阿拉伯语，输出 full-pack 全部尺寸：`/path/to/ad.png`

交付 profile：`standard-delivery`（1:1、16:9、4:5、9:16、1.91:1）、`social-core`、`google-ads-image-assets`、`iab-display`（300x250、728x90、320x50、320x100、160x600、300x600、970x250）、`full-pack`。

每个任务都有独立的 run 文件夹：`final/` 放可直接投放的文件（命名为 `<slug>_<语言>_<宽x高>_<日期>.png`），`qa/` 放 preflight、render_qa、manifest、contact sheet 和视觉审核记录，`work/layers/` 放抠图与底图，`Flagged by Culture-Aware QA/` 放需要投放前人工复核的文件。

## License

MIT © kouzt123。用 Claude Code 构建。
