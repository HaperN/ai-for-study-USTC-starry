利用公式(4.6)，可以仿照解析函数的最大模原理，给出调和函数极值原理的证明，在此不再重复了。下面利用最大模原理证明。

假定调和函数 $u(z)$ 在 $D$ 内的点 $z_0$ 达最大值。在 $D$ 内作圆 $G: |z - z_0| < \rho, 0 < \rho < +\infty$，并作 $G$ 内以 $u$ 为实部的解析函数 $f(z)$，于是在 $G$ 内的解析函数 $e^{f(z)}$ 的模在 $z_0$ 达最大值 $\mathrm{e}^{u(z_0)}$（由 $\left|\mathrm{e}^{f(z)}\right|=\mathrm{e}^{u(z)}$ 便知，这正是“$u$ 在 $z_0$ 取到最大值”的另一种写法）。但由于 $u(z)$ 不恒等于常数，从而 $f(z)$ 及 $e^{f(z)}$ 也不恒等于常数，这就与最大模原理矛盾。所以 $u(z)$ 只只能在 $D$ 的边界 $C$ 上取到 $C + D$ 上的最大值。考虑函数 $-u(z)$ 就可知 $u(z)$ 只能在 $C$ 上取到 $C + D$ 上的最小值。

[[DIAGRAM:D-01]]

这里的推理链有四步，缺一步都接不上。[[NOTE:N-03]]

第一步，$\mathrm{e}^{f(z)}$ 也是解析函数，因为解析函数的复合仍是解析函数。第二步，$\left|\mathrm{e}^{f(z)}\right|=\mathrm{e}^{u(z)}$。指数函数是增函数，所以 $u$ 在哪一点最大，$\left|\mathrm{e}^{f(z)}\right|$ 就在同一点最大。第三步，假设 $u$ 的最大值在内部点 $z_0$ 达到。那么 $\left|\mathrm{e}^{f(z)}\right|$ 的最大值也在内部点 $z_0$ 达到。第四步，最大模原理说：不恒等于常数的解析函数，模不可能在区域内部取到最大值。第三步与第四步矛盾，所以假设不成立。

调和函数也有类似于柯西积分公式的结果：

定理6 设 $u(z)$ 是闭圆 $\bar{D}: |z - z_0| \leqslant R$ 上的调和函数，则对此圆内部任一点 $z = z_0 + r\mathrm{e}^{\mathrm{i}\varphi}(r < R)$，有
$$\begin{array}{r} {u (r, \varphi) = \frac {1}{2 \pi} \int_ {0} ^ {2 \pi} u (R, \theta)   \frac {R ^ {2} - r ^ {2}}{R ^ {2} - 2 R r \cos (\theta - \varphi) + r ^ {2}} \mathrm{d} \theta ,} \\ {\text {里记} u (z _ {0} + r \mathrm{e} ^ {\mathrm{i} \varphi}) = u (r, \varphi),   u (z _ {0} + R \mathrm{e} ^ {\mathrm{i} \theta}) = u (R, \theta).} \end{array}\tag{4.7}$$

这个公式解决的是平均值定理解决不了的事。平均值公式只告诉你在圆心处的值，而 $r>0$ 的点够不着。泊松公式把手伸到圆内任一点：只要报出圆周上的全部值 $u(R,\theta)$，圆内每一点的值都由这个积分算出来 [[EXPLAIN:F-02|泊松积分公式]] [[CONCEPT:C-0229]]。

证 在 $\overline{D}$ 内作出以 $u(z)$ 为实部的解析函数 $f(z)$，对圆周 $C: |z - z_0| = R$ 内部任一点 $z = z_0 + r \mathrm{e}^{\mathrm{i}\varphi}, r < R$（其中 $\varphi$ 是该点相对于圆心 $z_0$ 的辐角），由柯西积分公式 [[CONCEPT:C-0215]]，得
$$\begin{array}{l} f (z) = \frac {1}{2 \pi \mathrm{i}} \int_ {C} \frac {f (\zeta)}{\zeta - z} \mathrm{d} \zeta \\ = \frac {1}{2 \pi} \int_ {0} ^ {2 \pi} f (z _ {0} + R e ^ {\mathrm{i} \theta}) \frac {R e ^ {\mathrm{i} \theta}}{R e ^ {\mathrm{i} \theta} - r e ^ {\mathrm{i} \varphi}} \mathrm{d} \theta . \end{array}\tag{4.8}$$

柯西积分公式说的是：闭曲线内部一点处的函数值，等于函数沿这条曲线的积分乘 $\frac{1}{2\pi\mathrm{i}}$，积分核是 $\frac{1}{\zeta-z}$。用它需要两个条件：那个点在曲线内部，函数在闭区域上解析。这里从 $\mathrm{d}\zeta$ 换成 $\mathrm{d}\theta$ 用了一次代换：圆周上 $\zeta=z_0+R\mathrm{e}^{\mathrm{i}\theta}$，于是 $\mathrm{d}\zeta=\mathrm{i}R\mathrm{e}^{\mathrm{i}\theta}\mathrm{d}\theta$。分母上的 $2\pi\mathrm{i}$ 与这个 $\mathrm{i}$ 恰好约掉，剩下 $\frac{1}{2\pi}$。

令 $z_{1} = z_{0} + R^{2}\mathrm{e}^{\mathrm{i}\varphi} / r$ ，由于 $\mid z_1 - z_0\mid = R^2 /r > R$ ，所以 $z_{1}$ 在圆 $C$ 的外部 [[NOTE:N-04]]，因此函数 $f(\zeta) / (\zeta -z_1)$ 在 $\overline{D}$ 上解析，由柯西积分定理 [[EXPLAIN:F-03|柯西积分定理]] [[CONCEPT:C-0207]]，得
$$\begin{array}{l} 0 = \frac {1}{2 \pi \mathrm{i}} \int_ {C} \frac {f (\zeta)}{\zeta - z _ {1}} \mathrm{d} \zeta \\ = \frac {1}{2 \pi} \int_ {0} ^ {2 \pi} f (z _ {0} + R e ^ {\mathrm{i} \theta}) \frac {R e ^ {\mathrm{i} \theta} \mathrm{d} \theta}{R e ^ {\mathrm{i} \theta} - \frac {R ^ {2}}{r} e ^ {\mathrm{i} \varphi}} \\ = \frac {1}{2 \pi} \int_ {0} ^ {2 \pi} f (z _ {0} + R e ^ {\mathrm{i} \theta}) \frac {r e ^ {\mathrm{i} \theta}}{r e ^ {\mathrm{i} \theta} - R e ^ {\mathrm{i} \varphi}} \mathrm{d} \theta , \end{array}\tag{4.9}$$

柯西积分定理说的是：函数在一个闭曲线所围的区域内解析、在闭区域上连续时，沿这条闭曲线的积分是零。上面这个函数在 $\bar{D}$ 内没有奇点，因为它的分母 $\zeta-z_1$ 的零点 $z_1$ 落在圆周外面。

[[DIAGRAM:D-02]]

为什么添这一项？因为 $z_1$ 在圆外，同一个 $f$ 就多出一个等于零的积分表达式。(4.8)与(4.9)相减，被积函数相同，可以逐点相减，得到一个不那么简单的核。

从(4.8)式减去(4.9)式可得
$$f (z) = \frac {1}{2 \pi} \int_ {0} ^ {2 \pi} f (z _ {0} + R e ^ {i \theta}) \left(\frac {R e ^ {i \theta}}{R e ^ {i \theta} - r e ^ {i \varphi}} - \frac {r e ^ {i \theta}}{r e ^ {i \theta} - R e ^ {i \varphi}}\right) d \theta .$$
$$\frac {R \mathrm{e} ^ {\mathrm{i} \theta}}{R \mathrm{e} ^ {\mathrm{i} \theta} - r \mathrm{e} ^ {\mathrm{i} \varphi}} - \frac {r \mathrm{e} ^ {\mathrm{i} \theta}}{r \mathrm{e} ^ {\mathrm{i} \theta} - R \mathrm{e} ^ {\mathrm{i} \varphi}} = \frac {R ^ {2} - r ^ {2}}{R ^ {2} - 2 R r \cos (\theta - \varphi) + r ^ {2}},$$

括号里的两个分式先通分。通分后的分子为 $R r \mathrm{e}^{\mathrm{i}2\theta}-R^{2}\mathrm{e}^{\mathrm{i}(\theta+\varphi)}-R r \mathrm{e}^{\mathrm{i}2\theta}+r^{2}\mathrm{e}^{\mathrm{i}(\theta+\varphi)}=(r^{2}-R^{2})\mathrm{e}^{\mathrm{i}(\theta+\varphi)}$，其中两项 $R r \mathrm{e}^{\mathrm{i}2\theta}$ 正好抵消，剩下的确实与 $R^{2}-r^{2}$ 成比例。分母 $(R \mathrm{e}^{\mathrm{i}\theta}-r \mathrm{e}^{\mathrm{i}\varphi})(r \mathrm{e}^{\mathrm{i}\theta}-R \mathrm{e}^{\mathrm{i}\varphi})$ 展开后也含公因子 $\mathrm{e}^{\mathrm{i}(\theta+\varphi)}$，把它提出来，括号里是 $R r(\mathrm{e}^{\mathrm{i}(\theta-\varphi)}+\mathrm{e}^{-\mathrm{i}(\theta-\varphi)})-R^{2}-r^{2}$；再用 $\mathrm{e}^{\mathrm{i}(\theta-\varphi)}+\mathrm{e}^{-\mathrm{i}(\theta-\varphi)}=2\cos(\theta-\varphi)$，括号里就等于 $2Rr\cos(\theta-\varphi)-R^{2}-r^{2}$。上下把公因子 $\mathrm{e}^{\mathrm{i}(\theta+\varphi)}$ 约掉，再上下同时变号，复值分式就化成右边那个实值分式。

$$f (z) = \frac {1}{2 \pi} \int_ {0} ^ {2 \pi} f (z _ {0} + R c ^ {i \theta}) \frac {R ^ {2} - r ^ {2}}{R ^ {2} - 2 R r \cos (\theta - \varphi) + r ^ {2}} d \theta .$$
再将上式两边取实部即得(4.7)式。
