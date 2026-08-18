---
name: visual-prompt-foundations
description: 通用视觉Prompt工程基础。涵盖图片/视频prompt结构化写法、中英双语电影摄影术语词表、各模型prompt最佳长度、禁忌清单。所有风格和内容类型的共享基础
---

# 通用视觉 Prompt 工程基础

本 skill 是所有视觉生成（图片/视频）的共享基础知识。风格 skill（style-*）和内容类型 skill（type-*）在此基础上叠加各自领域的规范。

## 1. Prompt 结构骨架

### 1.1 图片 Prompt 三层构建法

每张图片 prompt 由三层组成，逐层递进：

**Part 1：场景风格指令**（从 Shot 的实际字段提取：`framing` 景别、`camera`/`camera_description` 运镜；光照、景深、质感在 prompt 中补充描述）
- 景别（framing）+ 光照描述 + 景深描述 + 质感描述
- 示例：`medium close-up, golden hour warm lighting, shallow depth of field, film grain`

**Part 2：视觉一致性锚点**（从项目 `visual.visual_bible` / `visual.style` 提炼，适配不照搬）
- 提取项目视觉语言的本质（5-10 词），保持跨场景视觉统一
- 示例：项目风格为"Clean, minimal illustration with soft shadows" → 锚点为 `muted color palette, soft shadows`
- **禁止**将 `visual.visual_bible` 整段复制到每个 prompt

**Part 3：场景描述**（具体主体 + 动作 + 环境）
- 用具体细节替代笼统词汇
- 错误：`A person using a computer in a modern office`
- 正确：`Software developer in a dimly lit home office, blue monitor glow reflecting off glasses, desk cluttered with energy drinks`

### 1.2 视频 Prompt 五要素骨架

基于 CMU/Harvard 论文（"Building a Precise Video Language with Human-AI Oversight"），VLM 对主体+场景的描述可靠，但对运动、空间、镜头的描述不可靠。**强制 prompt 填满全部五个槽位是最高杠杆的改进。**

| 要素 | 说明 | 示例 |
|------|------|------|
| **[Subject]** | 主体类型 + 关键视觉属性 + 多主体时的区分方式 | `A weathered fisherman in his 60s, salt-and-pepper beard, dark wool sweater` |
| **[Subject Motion]** | 时间顺序的动作；主体间互动 | `He pulls the rope hand-over-hand, muscles straining, then pauses and looks out to sea` |
| **[Scene]** | 场景叠加 + POV + 设定 + 时间 + 场景动态 | `Wooden dock at dawn, calm grey ocean, distant fog bank, seagulls wheeling overhead` |
| **[Spatial]** | 景别 + 画面位置 + 景深（FG/MG/BG）+ 机位高度 + **这些如何变化** | `Medium close-up, slight low angle, subject center-frame, shallow DOF with dock posts in FG` |
| **[Camera]** | 回放速度 → 镜头畸变 → 高度 → 角度 → 焦点/DoF → 稳定度 → 运动 | `Slow dolly-in, eye-level, rack focus from rope to distant horizon, steady` |

**短 prompt = 更多创作自由。长 prompt = 更多控制。**

### 1.3 各模型 Prompt 最佳长度

| 模型 | 图片 prompt | 视频 prompt | 备注 |
|------|-----------|-----------|------|
| HappyHorse | 80-150 词 | 150-300 词 | 参考指代用 [Image N] 格式 |
| Wan | 80-150 词 | 200-400 词 | 微调于长 caption，奖励详细描述 |
| Seedance2 | 80-150 词 | 200-400 词（主镜头）/ 80-150 词（插入） | 奖励结构化 5 要素 prompt |
| FLUX | 20-40 词 | N/A | 超过 2-3 句效果下降 |
| DashScope Image | 30-60 词 | N/A | 简洁直接 |
| OpenAI Image | 20-40 词 | N/A | 指令跟随能力强，复杂构图可用自然语言 |

## 2. 电影摄影术语词汇表（中英双语）

### 2.1 景别（13 种）

| 中文 | English | 使用场景 |
|------|---------|---------|
| 大远景 | extreme wide shot | 建立环境规模，主体极小 |
| 远景 | wide shot / establishing shot | 开场建立场景，展示地点 |
| 全景 | full body shot / long shot | 主体头到脚完整可见 |
| 中全景 | medium full shot | 膝盖以上，展示肢体语言 |
| 中景 | medium shot / waist up | 腰部以上，平衡细节与环境 |
| 中近景 | medium close-up / chest up | 胸部以上，对话亲密感 |
| 近景 | close-up | 面部或关键物体，强调情绪 |
| 大特写 | extreme close-up | 孤立细节（眼睛、水滴、纹理） |
| 过肩 | over-the-shoulder (OTS) | 对话构图，连接感 |
| 主观视角 | point-of-view (POV) | 观众成为角色 |
| 鸟瞰 | bird's-eye / top-down | 地图式概览，全知感 |
| 虫视 | worm's-eye view | 直视上方，强调高度 |
| 荷兰角 | Dutch / canted angle | 倾斜地平线，不安或紧张 |

### 2.2 镜头运动

**关键区分：dolly ≠ zoom。dolly 是摄像机物理平移；zoom 是焦距变化。pan ≠ truck。pan 是原地旋转；truck 是横向平移。**

| 分类 | 中文 | English | 说明 |
|------|------|---------|------|
| **平移**（摄像机物理移动） | 推镜头 | dolly in / tracking in | 摄像机向主体靠近 |
| | 拉镜头 | dolly out / tracking out | 摄像机远离主体 |
| | 横移 | truck left / truck right | 摄像机横向平移 |
| | 升降 | pedestal up / pedestal down | 摄像机垂直升降 |
| **旋转**（摄像机原地转动） | 左摇 | pan left | 摄像机向左旋转 |
| | 右摇 | pan right | 摄像机向右旋转 |
| | 上摇 | tilt up | 摄像机向上旋转 |
| | 下摇 | tilt down | 摄像机向下旋转 |
| | 滚转 | roll CW / roll CCW | 摄像机顺/逆时针滚动 |
| **镜头**（无摄像机移动） | 变焦推 | zoom in | 焦距变化，非摄像机移动 |
| | 变焦拉 | zoom out | 焦距变化 |
| | 移焦 | rack focus | 焦点在两个主体间切换 |
| | 跟焦 | focus tracking | 焦点跟随运动主体 |
| **混合/特殊** | 环绕 | arc / orbit | 围绕主体弧形运动 |
| | 一镜到底 | long take / oner | 无剪切连续拍摄 |
| | 希区柯克变焦 | dolly zoom / vertigo | 推镜头+拉变焦，空间扭曲感 |
| | 升降摇臂 | crane shot | 摇臂升降运动 |
| | 甩镜 | whip pan | 快速摇摄产生运动模糊 |
| | 跟拍 | tracking / follow shot | 跟随主体运动 |
| | 手持 | handheld | 手持拍摄，微晃真实感 |
| | 稳定器 | steadicam | 稳定器跟拍，流畅 |
| | FPV 穿越机 | FPV drone | 穿越机高速穿越 |
| **静止** | 固定机位 | static / locked-off | **严格要求**：零移动、零焦点变化、零变焦 |

### 2.3 机位高度（7 级）

| 中文 | English | 示例 |
|------|---------|------|
| 航拍高度 | aerial-level | `drone-altitude wide of the city` |
| 屋顶高度 | overhead-level | `rooftop height looking across` |
| 人眼高度 | eye-level | `framed at eye level` |
| 臀部高度 | hip-level | `hip-height tracking shot` |
| 地面高度 | ground-level | `low to the ground, ankle height` |
| 水面高度 | water-level | `skimming the water surface` |
| 水下 | underwater | `submerged below the surface` |

### 2.4 机位角度（7 种）

| 中文 | English | 定义 |
|------|---------|------|
| 鸟瞰 | bird's-eye | 严格正上方俯视。注意：不等于 aerial |
| 高角度 | high angle | 俯视主体 |
| 平角度 | level angle | 摄像机与主体同高 |
| 低角度 | low angle | 仰视主体 |
| 虫视 | worm's-eye | 严格正下方仰视（贴近地面）。注意：比 low angle 更极端 |
| 荷兰角（固定） | Dutch angle (fixed) | 倾斜地平线保持不动 |
| 荷兰角（滚动） | Dutch angle (rolling) | 地平线倾斜角度变化 |

### 2.5 光照体系（12+ 种）

| 中文 | English | 效果 |
|------|---------|------|
| 自然光 | natural light | 柔和、真实（晨光/阴天/月光） |
| 黄金时段 | golden hour | 暖色阳光、长阴影、浪漫 |
| 高调光 | high-key | 明亮、均匀、欢快——喜剧、生活 |
| 低调光 | low-key | 暗调、高对比——惊悚、剧情 |
| 伦勃朗光 | Rembrandt | 面颊三角光，经典肖像 |
| 黑色电影光 | film noir | 深阴影、强高光 |
| 体积光 | volumetric | 可见光束穿过大气（雾、尘） |
| 逆光 | backlighting | 主体后方光源，剪影效果 |
| 侧光 | side lighting | 强方向性，戏剧性阴影 |
| 实景光 | practical lights | 画面上可见光源（灯、蜡烛、霓虹） |
| 轮廓光 | rim / edge light | 勾勒主体轮廓，分离背景 |
| 蝴蝶光 | butterfly / paramount | 正上方光源，鼻下蝴蝶形阴影 |

### 2.6 镜头效果

| 中文 | English | 效果 |
|------|---------|------|
| 广角镜头 | wide-angle (24-35mm) | 更宽视角，夸张透视 |
| 长焦镜头 | telephoto (85mm+) | 压缩透视，主体隔离 |
| 变形宽银幕 | anamorphic | 拉伸画幅，标志性镜头光晕 |
| 镜头光晕 | lens flare | 强光射入镜头产生的条纹 |
| 鱼眼 | fisheye | 极端曲面，边缘强烈弯曲 |
| 桶形畸变 | barrel distortion | 轻度畸变，直线向外弯曲 |
| 深焦 | deep focus | 前后景全部锐利 |
| 浅景深 | shallow DOF | 主体锐利，背景虚化 |
| 极浅景深 | extremely shallow DOF | 极薄焦平面 |
| 移焦 | rack focus | 拍摄中焦点在两个主体间切换 |

### 2.7 时间效果（6 种）

| 中文 | English | 定义 |
|------|---------|------|
| 延时 | time-lapse | 事件显著快于真实时间 |
| 快进 | fast-motion | 略快于真实（1x-3x） |
| 慢动作 | slow-motion | 慢于真实 |
| 定格 | stop-motion | 逐帧离散运动 |
| 速度渐变 | speed-ramp | 同一镜头内快慢混合 |
| 倒放 | time-reversed | 逆向播放 |

### 2.8 审美控制词表速查

- **光源类型**：日光 / 火光 / 阴天光 / 晴光 / 黎明光 / 黄昏光 / 霓虹灯光 / 月光 / 实景光 / 混合光
- **光线类型**：柔光 / 硬光 / 侧光 / 逆光 / 轮廓光 / 顶光 / 底光 / 蝴蝶光 / 明暗对比光(chiaroscuro)
- **构图方式**：中心构图 / 左右侧重构图 / 对称构图 / 三分法 / 框中框 / 过肩构图 / 负空间 / 引导线 / 前景遮挡
- **镜头焦距**：超广角鱼眼 / 广角 / 标准 / 中焦 / 长焦 / 压缩长焦
- **拍摄角度**：平视 / 低角度仰拍 / 高角度俯拍 / 鸟瞰 / 过肩角度 / 第一人称主观视角
- **色调**：暖色调 / 冷色调 / 低饱和度 / 高饱和度 / 高对比度 / 低对比度 / 金银色调 / 青橙色调

### 2.9 动态控制参考词

- **运动类型**：行走 / 奔跑 / 跳跃 / 旋转 / 挥手 / 舞蹈 / 飞翔 / 游泳 / 攀爬 / 跌倒 / 翻滚
- **角色情绪**：高兴 / 悲伤 / 惊讶 / 愤怒 / 平静 / 紧张 / 恐惧 / 期待 / 满足 / 困惑
- **运动速度**：缓缓 / 逐渐加速 / 匀速 / 快速 / 突然 / 缓慢减速 / 定格

## 3. 身份锚定（多镜头/多帧一致性）

模型在跨镜头时会丢失角色身份，除非你重新声明。**在每个镜头中重复相同的 3-6 个区分性视觉属性。** 代词和"同一角色"不起作用。

**错误**：`Aang plants his staff. ... Aang turns to camera.`
**正确**：`Aang — bald, blue arrow tattoo on forehead, orange-and-yellow robes — plants his staff. ... Aang — bald, blue arrow tattoo on forehead, orange-and-yellow robes — turns to camera.`

## 4. "不要做什么"清单

| 不要写 | 原因 | 替代方案 |
|--------|------|---------|
| `Beautiful scene` | 太模糊，无视觉信息 | `Wet cobblestone street, warm streetlamp glow reflecting in puddles` |
| `Person moves quickly` | 无可视动作 | `Woman sprints three steps and vaults over the railing` |
| `Cinematic look` | 每个模型默认就尝试这个 | 指定：`anamorphic lens, shallow DOF, golden hour lighting` |
| `Sad character` | 内心状态不可见 | `Tears on cheek, shoulders slumped, staring at empty chair` |
| `Epic` | 不约束像素 | `Low-angle, 24mm wide, sun directly behind subject, lens flare on the rim` |
| 可读文字 / 标志 | 模型无法可靠渲染文字 | 在合成阶段用 overlay 叠加文字 |
| 复杂物理效果 | 混沌运动导致伪影 | 保持物理简单；舞蹈/行走 OK，爆炸有风险 |
| 多人同时说话 | 多人对话破坏同步 | 每镜头一个说话者，或用反应镜头 |
| 过度拥挤的 prompt | 太多元素 = 不连贯 | 从简单开始，逐层添加复杂度 |
| 矛盾的光照 | `Bright noon` + `dark shadows` | 选一种光照方案并坚持 |
| `static camera` + 任何运动 | static 严格要求零运动 | 如有任何移动，选择正确的运动原语 |

## 5. 风格与美学参考

### 电影风格
- Film noir, period drama, thriller, modern romance
- Documentary, arthouse, experimental film
- Epic space opera, fantasy, horror
- 1970s romantic drama, 90s documentary-style

### 动画风格
- Studio Ghibli / Japanese anime
- Classic Disney, Pixar-like 3D
- Stop-motion, claymation
- Hand-painted 2D/3D hybrid
- Cel-shaded, low-poly 3D

### 艺术运动
- Impressionistic, surrealist, Art Deco, Bauhaus
- Watercolor, charcoal sketch, ink wash
- Graphic novel, blueprint schematic

### 胶片/调色
- Kodak warm grade, Fuji cool tones
- 16mm black-and-white, 35mm photochemical contrast
- Vintage grain overlay, halation on speculars
- Teal-and-orange color grade

## 6. 画质规范

**使用明确画质词**：`high resolution, sharp focus, detailed texture, clean lines`

**避免使用可能引导画质降级的词汇**：
- `blurry`、`out of focus`（除非画面意图如此）
- `watercolor`、`sketch`、`low poly`（偏离当前风格意图时）
- 不必要的风格叠加词
