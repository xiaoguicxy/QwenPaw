---
name: style-3d-realistic-cgi
description: 写实CG渲染风格。光线追踪、物理材质、真实光照、高精度。适用：建筑可视化/产品渲染/科幻大片。当用户要求写实CG/照片级/光线追踪/建筑可视化时激活。
---

# 写实 CG 渲染 · 视觉风格

## 1. 风格定义

- **世界观/美学基调**：追求照片级真实感的CG渲染。所有材质、光照、物理效果都模拟真实世界。
- **核心视觉特征**：光线追踪, 物理材质, 真实光照, 高精度, 照片级
- **参考作品**：见各风格经典影片/动画

## 2. 严禁内容

以下元素与本风格冲突，不得在 prompt 中使用：
- 卡通/风格化
- 扁平着色
- 低精度
- 明显CG感（塑料质感）

## 3. 图片 Prompt 规范

### 3.1 风格前缀锚点（5-10 词）
所有图片 prompt 共享的风格方向（适配不照搬）：
`光线追踪, 物理材质, 真实光照, 写实 CG 渲染 aesthetic`

### 3.2 色彩体系
真实世界色彩。精确的色彩还原。环境反射和折射影响色彩。

### 3.3 光照体系
物理正确的光照。全局光照、光线追踪。自然光和人工光源混合。精确的阴影和反射。

### 3.4 角色设计 prompt 规范
照片级人物（注意恐怖谷）。精确的皮肤SSS、毛发模拟。或用于产品/建筑无人物场景。

### 3.5 场景设计 prompt 规范
照片级环境。精确的材质纹理。全局光照。物理正确的反射/折射。

### 3.6 分镜图 prompt 规范
- 分镜图使用本风格的色彩和光照基准
- 画质限定词与风格匹配
- 相邻宫格保持风格一致

## 4. 视频 Prompt 规范

### 4.1 视频风格锚点（1-2 句）
写实CG渲染，物理材质，光线追踪光照，照片级真实感。

### 4.2 运镜偏好
模拟真实摄影机参数。焦距/光圈/ISO可指定。景深、运动模糊、镜头畸变。

### 4.3 色彩/光影方向
与图片保持一致的色彩体系和光照方向。视频中光影可以随叙事变化，但风格基调不变。

## 5. 完整 Prompt 示例

### 5.1 角色/场景设计图 prompt 示例
```
Wide shot, photorealistic CGI. A futuristic glass skyscraper reflecting sunset, sleek metallic entrance, landscaped plaza with water fountain. Physically accurate materials, glass reflections, wet pavement, volumetric light. Virtual Canon EOS R5, 24mm, f/8. 16:9.
```

### 5.2 视频 prompt 示例
```
Shot 1 (wide, slow aerial push-in, 4s):
Photorealistic CGI. A luxury sports car with mirror-finish chrome races along a coastal highway at golden hour. Every surface reflects environment. Tire spray catches sunlight.

Shot 2 (medium close-up, tracking, 3s):
Camera low beside front wheel. Road rushes past. Brake caliper glows red. Heat haze behind engine.

Style: Photorealistic CGI, ray-traced reflections, physically accurate materials, volumetric, cinematic lens.
Audio: Engine roar, tire friction, coastal wind, distant waves.
```
