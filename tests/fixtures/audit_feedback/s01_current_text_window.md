由线积分的结论，积分对上限的偏导数就是被积表达式里的 $P$ 与 $Q$：$\frac{\partial v}{\partial x}=P=-\frac{\partial u}{\partial y}$，$\frac{\partial v}{\partial y}=Q=\frac{\partial u}{\partial x}$。这两条正是 C-R 方程。这里要用到上一节已证的一条反向结论：若 $u$、$v$ 在区域 $D$ 内有一阶连续偏导数，并且处处满足 C-R 方程，则 $f(z)=u+\mathrm{i}v$ 在 $D$ 内解析。本证明中 $v$ 的两个一阶偏导正是被积表达式里的 $P=-\frac{\partial u}{\partial y}$ 与 $Q=\frac{\partial u}{\partial x}$，它们在 $D$ 内连续，所以这条结论的条件满足，$f(z)=u+\mathrm{i}v$ 在 $D$ 内解析。

[[DIAGRAM:D-02]]

给虚部的情形做法一样。若已给单连通域 $D$ 内的调和函数 $v(x,y)$，则由线积分所确定的函数

$$u (x, y) = \int_ {(x _ {0}, y _ {0})} ^ {(x, y)} \frac {\partial v}{\partial y} d x - \frac {\partial v}{\partial x} d y + C _ {1},\tag{4.5}$$

使得 $f(z)=u+\mathrm{i}v$ 在 $D$ 内解析，其中 $C_1$ 是实常数。检查的办法一模一样：$v$ 调和保证积分与路径无关，积分对上限求偏导得到的两条等式就是 C-R 方程。

把这些结果合起来说：已知单连通域 $D$ 内的调和函数，就能找到在 $D$ 内解析的函数 $f(z)$，它以这个已知调和函数为实部或虚部。这个 $f(z)$ 可能相差一个实常数或纯虚常数。

如果 $D$ 是多连通域，也就是区域里有洞，(4.4) 或 (4.5) 里的线积分就与路线有关了。同一个终点沿不同路线走可能算出不同的值，这时 $v$ 是多值函数，得到的解析函数 $f(z)$ 也是多值的。本节不深入讨论这种情形。

例 2　已知 $v=2x^2-2y^2+x$，求以它为虚部、并且满足 $f(0)=1$ 的解析函数 $f(z)$。先验证 $v$ 是调和函数：$\frac{\partial^{2} v}{\partial x^{2}}=4$，$\frac{\partial^{2} v}{\partial y^{2}}=-4$，相加为 0，所以它有资格当一个解析函数的虚部。接下来就用 (4.5) 把 $u$ 积出来，并把满足 $f(0)=1$ 的那一个 $f(z)$ 一路定到常数确定为止。积分路线取从 $(0,0)$ 到 $(x,y)$ 的折线：先沿 $x$ 轴走到 $(x,0)$，再竖直走到 $(x,y)$。第一段上 $\mathrm{d}y=0$，而 $\frac{\partial v}{\partial y}=-4y$ 在 $y=0$ 上恒为 0，这一段没有贡献；第二段上 $\mathrm{d}x=0$，只留下 $\frac{\partial v}{\partial x}=4x+1$ 的贡献。代入 (4.5) 得

$$u(x,y)=\int_{0}^{x}\frac{\partial v}{\partial y}(t,0)\,\mathrm{d}t-\int_{0}^{y}\frac{\partial v}{\partial x}(x,s)\,\mathrm{d}s+C=-\int_{0}^{y}(4x+1)\,\mathrm{d}s+C=-4xy-y+C,$$

其中 $C$ 就是 (4.5) 里的实常数 $C_1$。拼起来得 $f(z)=u+\mathrm{i}v=-4xy-y+C+\mathrm{i}(2x^{2}-2y^{2}+x)=2\mathrm{i}z^{2}+\mathrm{i}z+C$。最后用 $f(0)=1$：代入 $z=0$ 得 $f(0)=C$，于是 $C=1$，所求的解析函数为 $f(z)=2\mathrm{i}z^{2}+\mathrm{i}z+1$。[[EXAMPLE:E-02]]

[[FIGURE:IMG-0044]]

本节得到的 (4.4) 与 (4.5) 是把调和函数和解析函数连起来的一座桥。后面遇到只给出实部或虚部的题目，就从这两条线积分公式入手。
