---
name: image-generation
description: 图片生成专项最佳实践。涵盖视觉一致性策略、hero参考图法、分镜图/阵容图/资产图最佳实践、画质控制。
---

# 图片生成专项最佳实践

本 skill 指导如何高质量地生成各类图片（角色/场景/道具资产图、阵容图、分镜图）。

## 1. 视觉一致性策略

### 1.1 Hero 参考图法（推荐）

1. 生成一张最高质量的主图（hero image）
2. 后续所有帧以此为 `input_image` 参考：
   - Frame 1: T2I 详细 prompt → hero.png
   - Frame 2: I2I with hero.png + "Same style, camera pans right to show..."
   - Frame 3: I2I with hero.png + "Same style, zoomed in on..."
3. FLUX.2 支持最多 4 个参考（klein）或 8 个参考（pro/max/flex），可按编号引用

### 1.2 共享视觉系统

先定义项目的共享视觉系统，再逐场景适配：
- 主色调方向
- 光照基调
- 渲染媒介（胶片/数字/手绘/3D）
- 角色/环境一致性锚点

**关键原则**：项目的 `visual.visual_bible` / `visual.style` 是源材料，不是逐字复制的模板。提炼其本质（5-10 词），适配到每个场景。

### 1.3 Seed 锁定

使用相同 `seed` 参数 + 相似 prompt → 相似构图。作为辅助策略，不作为主要策略。

## 2. 参考图链使用规范

参考图解析顺序（与 visual_reference_resolution 机制对齐）：

```
Lineup 锚点（首位）→ 实体 variant 选择 → 显式参考
```

- **Lineup 锚点**：阵容图作为参考链首位，承载多角色同框的群体一致性（相对身高、体型比例、风格基准）
- **实体 variant 选择**：每个角色贡献其 canonical variant 的 selected_artifact_version_id 作为身份锚点
- **显式参考**：filtered to avoid cross-variant conflicts（variant A 的参考不会污染 variant B 的生成）

**跨 variant 污染防护**：如果参考图属于 variant A，当 variant B 绑定时会被过滤掉。

## 3. 分镜图生成最佳实践

### 3.1 宫格布局规则

分镜图宫格数量必须**严格等于** Shot 数量：
- 1 Shot → 单张画面
- N Shot → N 宫格分镜图

**横屏（16:9 等）布局**：
- 4 宫格：2×2 网格
- 6 宫格：2×3 网格（上排 3 个、下排 3 个）
- 9 宫格：3×3 网格

**竖屏（9:16 等）布局**：
- 4 宫格：2×2 或 4×1 竖排
- 6 宫格：3×2 网格
- 9 宫格：3×3 网格

按 Shot 顺序排列：横屏先左后右、先上后下；竖屏先上后下、先左后右。

### 3.2 画面纯净性（硬性规则）

分镜图是 r2v 视频生成的纯画面参考；画面中的注释性文字会被视频模型继承并污染成片。

**永远禁止**：镜头编号、分格标签、标题、字幕、对白气泡、水印、说明文案。
**场景需要时允许**：属于画面世界本身的文字（球衣号码、记分牌、店招）。

**执行规则**：
- 不写会诱导注释性文字的字面标签
- 多镜头分镜用空间方位描述各格内容（左上格、右上格...）
- 无画内文字需求时，prompt 结尾附加：`No panel numbers, no captions, no labels, no subtitles, no watermarks, no annotation text in the image.`
- 有画内文字需求时，先显式声明内容与位置，结尾改用例外式约束

### 3.3 风格一致性控制

- 从项目 `visual.style` 提炼风格锚点（5-10 词）
- 每个宫格共享同一风格锚点
- 相邻宫格的主体外观、场景环境、道具位置保持一致
- 光线和色调配合内容节拍变化，但风格基调不变

## 4. 阵容图生成最佳实践

### 4.1 角色站位与比例

- `character_refs` 按期望站位排序（从左到右）
- `relative_notes` 必须写成**成对对比**：逐角色列出相对身高/体型
- 示例：`A:B ≈ 195:170cm，A 壮硕 B 瘦小`

### 4.2 风格基准统一

- 阵容图锁定角色间统一风格基准
- 所有角色使用相同的光照、色彩、渲染风格
- 阵容图是后续分镜的成对判别锚点

### 4.3 号码/背号

- 题材包含号码时，`relative_notes` 中同框角色的号码全部钉死
- 真实人物用真实号码
- 绝不只写部分角色的号码

## 5. 画质控制

### 5.1 画质限定词推荐

**通用**：`high resolution, sharp focus, detailed texture, clean lines`
**写实**：`photorealistic, natural skin texture, film grain, shallow depth of field`
**动画**：`clean line art, vibrant colors, smooth shading, consistent style`
**产品**：`studio lighting, accurate color reproduction, material detail, no distortion`

### 5.2 禁用降级词

以下词汇可能引导画质降级，避免使用（除非画面意图如此）：
- `blurry`、`out of focus`
- `watercolor`、`sketch`、`low poly`（偏离当前风格意图时）
- `amateur`、`snapshot`、`phone camera`
- 不必要的风格叠加词

### 5.3 不同风格类型的画质基线

| 风格类型 | 画质基线 |
|---------|---------|
| 写实/电影 | 胶片质感、自然光效、浅景深、35mm 颗粒 |
| 动画/卡通 | 干净线条、均匀上色、一致风格、无锯齿 |
| 产品/商业 | 高端质感、精准色彩、材质细节、无畸变 |
| 概念/特效 | 想象力丰富、细节精致、氛围到位 |

## 6. 常见陷阱

1. **文字渲染**：AI 图片生成器无法可靠渲染文字。永远不要在 prompt 中包含文字；文字在合成阶段用 overlay 叠加
2. **手指/手部**：AI 图片模型仍然难以处理。避免需要精细手部姿势的 prompt
3. **角色不一致**：没有参考图时，同一角色每次看起来都不同。始终使用 hero 参考图策略
4. **过度 prompt**：长而复杂的 prompt 产生不可预测的结果。保持在 2-3 句
5. **过度统一**：每个 prompt 强加完全相同的风格短语会让场景看起来一样。保持视觉系统一致，但让每个场景表达自己的主体、镜头和情感节拍
