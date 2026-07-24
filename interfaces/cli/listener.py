"""输入层：支持中文输入 + ESC 退出"""

import keyboard


class InputHandler:
    """CLI 输入处理器：读取一行用户输入，支持 ESC 取消。

    设计：全局挂钩 ESC 键，按下时置位 _esc_pressed 并模拟回车，
    使阻塞的 input() 立即返回；监听期间若按过 ESC，listen() 返回 None。
    类属性 _esc_pressed 在每次 listen 前重置。
    """
    _esc_pressed = False

    @classmethod
    def _on_esc(cls, e):
        """ESC 键按下回调：置位 _esc_pressed 标志并模拟回车，使 input() 立即返回。"""
        if e.event_type == "down":
            cls._esc_pressed = True
            keyboard.press_and_release("enter")

    @staticmethod
    def listen(prompt: str) -> str | None:
        """读取一行用户输入。

        参数: prompt - 输入提示符。
        返回: 用户输入字符串；若期间按下 ESC 则打印提示并返回 None（取消）。
        副作用: 临时全局挂钩 ESC 键，读取结束后解除挂钩。
        """
        InputHandler._esc_pressed = False
        keyboard.hook_key("esc", InputHandler._on_esc)
        try:
            text = input(prompt)
            if InputHandler._esc_pressed:
                print("ESC")
                return None
            return text
        finally:
            keyboard.unhook_key("esc")