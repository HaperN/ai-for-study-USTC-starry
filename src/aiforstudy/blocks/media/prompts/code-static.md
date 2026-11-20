静态制作要求：使用已安装的 Manim Community，定义唯一顶层 GeneratedScene 类，继承 Scene 或其适用派生类。from aifs_media_runtime import load_context，在 construct 中读取 context；它提供 request、approved_content、approved_models。用 self.add(...) 布置最终完整画面，可信 worker 保存真实 PNG，不制作 MP4。返回 steps=[]，不调用 media_step，不伪造动画分镜。不要调用 render()、save_image()，不要自行保存文件或设置输出目录、帧率和像素分辨率。绘图代码保持开放，可使用辅助类、函数、循环和 Manim 的数学对象。

使用 Axes.plot 绘制普通函数曲线；需要时使用 ParametricFunction、plot_implicit_curve、坐标点、箭头、切线或面积。数学坐标必须通过 axes.c2p 等映射，不把画布位置当函数值；涉及角度、长度、对称的几何图用等比例坐标。奇点、间断和定义域边界必须正确处理：分段作图，不跨渐近线连线，高频变化需足够采样，不以平滑掩盖错误。图形的尺度、标注和公式依据本次正文，示意参数明确为示意。

worker 默认白色背景、深色文字。中文用 Text(..., font='Noto Sans CJK SC', color='#172033')，公式用 MathTex，采用只依赖 amsmath/amssymb/xcolor 的最小 TexTemplate；不要把中文放进 MathTex，不要显示原始 LaTeX。坐标轴与引导线轻于主要曲线，原对象/比较对象/结果保持固定角色颜色，用标签或线型帮助区分。

配图先让读者看图形，说明文字保持简短和较小：公式与关键标签为主层级，辅助说明为次层级，补充解释放正文或相邻浮窗。不复制整段教材、证明或大标题，不默认多面板纵向堆叠。按宿主约450 CSS px的正式双栏尺寸检查最终对象大小，关键公式、下标与标签必须清楚；字号不能从780px宽的大预览直接照搬。画面过密时优先缩短说明、调整布局并把长解释留给正文，不缩小必要数学关系。尽量用几何位置、投影线和颜色对应来说明关系，避免图内再写一遍长解释。

按最终画面检查字号与位置：MathTex 的上下标、分式、根式需要实际留白；用 next_to、align_to、对象 get_left/get_right/get_top/get_bottom 等边界排布，不能只比较文字锚点。不得为了避让文字挪动数学点，应该移动标签或调整构图。检查最后 scale/arrange 后在电脑显示尺寸下的大小，不能仅靠高DPI挽救过密内容。关键对象、箭头方向与必要公式仍要辨认清楚；精简说明和较小的辅助字不等于删掉教学关系。

payload.example 是自带独立 context 的原创共轭对称静态样例，示范相同坐标比例、稳定配色、较小辅助说明和保留关键标签。它没有动画步骤；图题由正文提供。只学习方法，不把示例位置、符号、数值、主题或布局复制为当前请求。
