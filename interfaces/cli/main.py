"""终端版主循环：菜单 → 对话/角色/API配置"""

import asyncio
from app.session.session import ChatSession
from interfaces.cli.listener import InputHandler
from interfaces.cli.player import OutputHandler


class Menu:
    """终端交互菜单：展示会话状态与可选项，并调度对话/角色/API 配置等子流程。"""
    OPTIONS = [
        ("1", "开始对话", "chat"),
        ("2", "角色设定", "persona"),
        ("3", "API 配置", "api"),
        ("4", "退出", "exit"),
    ]

    def __init__(self, session: ChatSession):
        """构建菜单控制器，绑定会话、输入与输出处理器。

        参数: session - ChatSession 实例（对话与配置均作用于此会话）。
        """
        self.session = session
        self.input = InputHandler()
        self.output = OutputHandler()

    def show(self):
        """打印菜单界面：当前角色设定、API 配置概览及可选项 1-4。"""
        persona = self.session.get_persona()
        api = self.session.get_api_config()

        print()
        print("=" * 40)
        print("AI 语音助手 - 终端版")
        print("-" * 40)

        tag = "[自定义]" if persona["custom"] else "[默认]"
        p = persona["persona"]
        print(f"角色 {tag}: {p[:60]}{'...' if len(p) > 60 else ''}")

        if api["custom"]:
            print(f"API: {api['model']} @ {api['base_url']}")
        else:
            print("API: 默认 (DeepSeek)")

        print("-" * 40)
        for key, label, _ in self.OPTIONS:
            print(f"  [{key}] {label}")
        print("=" * 40)

    async def run(self):
        """主循环：展示菜单并读取选择，按 1/2/3/4 调度对话、角色、API 配置或退出。"""
        while True:
            self.show()
            choice = self.input.listen("选择 > ").strip()

            if choice == "1":
                await self.chat_loop()
            elif choice == "2":
                self.persona_menu()
            elif choice == "3":
                self.api_menu()
            elif choice == "4":
                print("再见！")
                break
            else:
                print("无效选项，请输入 1-4")

    async def chat_loop(self):
        """对话模式循环：显示当前角色，读取用户输入并调用 session.chat，
        打印回复并播放语音；按 ESC 返回菜单。
        """
        print("\n进入对话模式（按 ESC 返回菜单）")
        while True:
            persona = self.session.get_persona()
            tag = "[自定义]" if persona["custom"] else "[默认]"
            print(f"角色{tag}: {persona['persona']}")
            text = self.input.listen("你说 > ")
            if text is None:          # <-- ESC 返回 None
                print("已返回菜单")
                break
            text = text.strip()
            if not text:
                continue

            result = await self.session.chat(text)
            self.output.show(result["reply"])
            self.output.play_audio(result.get("audio", "") or "")
            print("\n")

    def persona_menu(self):
        """角色设定菜单：展示当前与历史角色，支持选择历史编号、自定义输入或留空恢复默认。"""
        persona = self.session.get_persona()
        history = self.session.get_persona_history()

        print(f"\n当前角色: {persona['persona']}")
        print(f"状态: {'自定义（临时）' if persona['custom'] else '默认'}")
        print()

        print("角色历史:")
        if history:
            print("以往角色:")
            for i, item in enumerate(history):
                print(f"  ─{'─' * 55}")
                print(f"  [{i + 1}] {item}")
            print(f"  ─{'─' * 55}")
        else:
            print("(暂无历史角色)")
        print()


        print("操作:")
        print("  输入编号选择历史角色")
        print("  输入新文字手动设定角色")
        print("  留空按回车恢复默认")
        print("  按下ESC 返回菜单")

        text = self.input.listen("角色 > ")
        if text is None:
            return
        text = text.strip()
        if not text:
            self.session.set_persona("")
            print("已恢复默认角色")
            return

        # 尝试匹配编号
        if history and text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(history):
                self.session.set_persona(history[idx])
                print(f"已选择角色: {history[idx][:60]}")
                return

        # 手动输入
        self.session.set_persona(text)
        print("角色已更新（临时，重启后恢复默认）")

    def api_menu(self):
        """API 配置菜单：展示当前配置，逐项输入 api_key/base_url/model 进行临时更新。"""
        api = self.session.get_api_config()
        print(f"\n当前 API 配置:")
        print(f"  Key:    {'已设置' if api['api_key'] else '默认'}")
        print(f"  URL:    {api['base_url'] or '默认'}")
        print(f"  Model:  {api['model'] or '默认'}")
        print(f"  状态:   {'自定义（临时）' if api['custom'] else '默认'}")
        print()
        print("逐个输入（留空按回车保留当前值，ESC 返回）:")

        key = self.input.listen("  API Key > ")
        if key is None:
            return
        key = key.strip()

        url = self.input.listen("  Base URL > ")
        if url is None:
            return
        url = url.strip()

        model = self.input.listen("  Model > ")
        if model is None:
            return
        model = model.strip()

        self.session.set_api_config(
            api_key=key,
            base_url=url,
            model=model,
        )
        print("API 配置已更新（临时，重启后恢复默认）")



async def main():
    """终端版程序入口：创建 CLI 会话并启动菜单主循环。"""
    session = ChatSession(source="cli")
    menu = Menu(session)
    await menu.run()


if __name__ == "__main__":
    asyncio.run(main())