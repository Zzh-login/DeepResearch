"""
领域层（Domain Layer）包入口。

本包封装与具体技术栈（LLM、数据库、Web 框架）无关的核心业务概念与纯逻辑，
是整个应用的"业务内核"，不 import 任何 Infrastructure 层（LLM 客户端 / 存储 / API）。

子包说明：
  - persona：角色身份、行为规则、输出格式的数据结构与 YAML 加载器（entity.py）
  - prompt ：system prompt 的拼装（builder.py）与 LLM 输出的规则校验（validator.py）
  - types  ：消息与记忆的结构化数据类型（Message / MemoryItem / ChatTurn）
  - memory ：对话记忆的抽象与处理（reducer / extractor / chat_memory）

设计原则：依赖倒置——上层（Infrastructure / Interface）依赖本层的定义，
本层只描述"是什么、怎么校验"，不关心"用什么实现"。
"""

# domain package
