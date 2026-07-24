"""
角色系统子包（domain.persona）。

封装"角色是谁、必须怎么做、输出长什么样"的配置与数据结构：
  - entity.py：PersonaConfig / Rule / FormatTemplate 三个 dataclass，
    以及从 data/*.yaml 加载这些配置的 load_persona / load_rules / load_format。

配置驱动设计：角色行为全部外挂到 YAML（persona.yaml / rules.yaml / format.yaml），
修改角色无需改代码，非程序员也可直接编辑。
"""

# domain.persona package
