这份文本是**这一次要你判断的全部输入**（截图已经附在同一条消息里）。

## 一、这些截图是什么

{{IMAGE_NOTES}}

## 二、候选清单（只挑了"不确定"的那些条，不是全本目录）

每条格式：
* `line`：行号（**回填时原样抄**；`recover` 用 `null`）
* `title` / `number` / `level`：机器认为的标题、编号、层级
* `sources`：哪几个源说了这一条（`pdf-bookmarks`=PDF 书签、`printed-toc`=目录页文字、`body-heading-shapes`=正文标题形状）
* `bookmarks` / `printed` / `body`：各源各自的写法（不一样的才列）
* `page`：页码（`printed` 是书上印的，`pdf` 是 PDF 页码）
* `why`：机器为什么把这条挑出来（**这是你要处理的问题**）

```json
{{SUSPECTS}}
```

## 三、冲突清单（同一条，两个源说得不一样）

```json
{{DISAGREEMENTS}}
```

## 四、这本书的规模（供你判断"截图上是不是还有没抽到的条目"）

```json
{{CONTEXT}}
```

请按系统提示里的格式，只输出那个 JSON。
