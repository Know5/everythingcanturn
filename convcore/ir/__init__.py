"""分域信息模型（IR）。

每个领域一个模型，不做全局 IR。原则是取所有格式表达力的**并集**：宁可模型里
有某些格式用不到的字段，也不能让本可无损的一对在枢纽处丢信息。

各模型直接放在子模块里，调用方按需导入 `convcore.ir.raster` /
`convcore.ir.document` / `convcore.ir.markup` / `convcore.ir.media` /
`convcore.ir.data`；这里不重复导出，避免形成第二套公共名字。
"""
