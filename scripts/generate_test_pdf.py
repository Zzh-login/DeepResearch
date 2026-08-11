"""生成知识库测试用 PDF —— 「人工智能基础概念手册」（约 10 页）。

用法:
    cd E:/robot_system
    python scripts/generate_test_pdf.py

生成路径: data/knowledge/test/ai_handbook.pdf

依赖: fpdf2（首次运行自动 pip install），支持中文。
"""

import os
import subprocess
import sys
from pathlib import Path

# ── 确保 fpdf2 可用 ──
try:
    from fpdf import FPDF
except ImportError:
    print("[install] 安装 fpdf2...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "fpdf2"])
    from fpdf import FPDF


# ═══════════════════════════════════════════════════════
# 中文 PDF 类
# ═══════════════════════════════════════════════════════

class ChinesePDF(FPDF):
    """支持中文字体的 PDF 生成器。"""

    def __init__(self):
        super().__init__()
        # 尝试多个常见中文字体路径（Windows）
        font_paths = [
            "C:/Windows/Fonts/simhei.ttf",       # 黑体
            "C:/Windows/Fonts/msyh.ttf",          # 微软雅黑
            "C:/Windows/Fonts/simsun.ttc",        # 宋体
            "C:/Windows/Fonts/simkai.ttf",        # 楷体
        ]
        self.font_added = False
        for fp in font_paths:
            if os.path.exists(fp):
                self.add_font("CJK", "", fp)
                self.add_font("CJK", "B", fp)
                self.font_added = True
                print(f"[font] 使用字体: {fp}")
                break

        if not self.font_added:
            print("[WARNING] 未找到中文字体，PDF 将不含中文内容。请安装中文字体。")

    def header(self):
        pass  # 不用默认 header

    def footer(self):
        self.set_y(-15)
        self.set_font("CJK", "", 9)
        self.cell(0, 10, f"— {self.page_no()} —", align="C")


# ═══════════════════════════════════════════════════════
# 内容生成
# ═══════════════════════════════════════════════════════

def chapter_title(pdf: ChinesePDF, text: str):
    """一级章节标题：大号加粗，段前留白。"""
    pdf.ln(4)
    pdf.set_font("CJK", "B", 18)
    pdf.multi_cell(0, 10, text, align="L")
    pdf.ln(2)

def section_title(pdf: ChinesePDF, text: str):
    """二级小节标题：中号加粗。"""
    pdf.set_font("CJK", "B", 13)
    pdf.multi_cell(0, 8, text, align="L")
    pdf.ln(1)

def body_text(pdf: ChinesePDF, text: str):
    """正文段落：11pt，行高 1.5 倍。"""
    pdf.set_font("CJK", "", 11)
    pdf.multi_cell(0, 7, text, align="L")
    pdf.ln(1)


def generate(pdf: ChinesePDF):
    """生成 10 页 PDF 内容。"""
    pdf.set_auto_page_break(auto=True, margin=20)

    # ════════ 第 1 页：封面 ════════
    pdf.add_page()
    pdf.ln(50)
    pdf.set_font("CJK", "B", 28)
    pdf.multi_cell(0, 14, "人工智能基础概念手册", align="C")
    pdf.ln(8)
    pdf.set_font("CJK", "", 14)
    pdf.multi_cell(0, 10, "知识库检索测试专用文档", align="C")
    pdf.ln(4)
    pdf.set_font("CJK", "", 11)
    pdf.multi_cell(0, 8, "本手册涵盖机器学习、深度学习、自然语言处理、计算机视觉、\n模型评估、数据预处理、模型架构、迁移学习与 AI 伦理九大主题。", align="C")
    pdf.ln(10)
    pdf.set_font("CJK", "", 12)
    pdf.multi_cell(0, 8, "—— 用于验证语义检索的 page_number 与 section_title 准确性 ——", align="C")

    # ════════ 第 2 页：第一章 ════════
    pdf.add_page()
    chapter_title(pdf, "第一章 机器学习基础")

    section_title(pdf, "1.1 机器学习三大范式")
    body_text(pdf,
        "机器学习是人工智能的核心分支，根据训练数据是否带有人工标注，可划分为三种主要范式："
        "监督学习、无监督学习和强化学习。\n\n"
        "监督学习（Supervised Learning）使用带有标签（Label）的训练数据来训练模型。"
        "每个训练样本由输入特征（Feature）和对应的目标标签组成。"
        "模型通过学习输入到输出的映射关系，对未见过的数据进行预测。"
        "典型任务包括分类（如垃圾邮件识别、图像分类）和回归（如房价预测、股票预测）。"
        "常见的监督学习算法有线性回归、逻辑回归、决策树、随机森林、支持向量机（SVM）和 K 近邻（KNN）等。\n\n"
        "无监督学习（Unsupervised Learning）处理的是没有标签的数据，目标是发现数据的内在结构或分布规律。"
        "由于缺少明确的监督信号，模型需要自行从数据中挖掘模式。"
        "典型任务包括聚类（Clustering）、降维（Dimensionality Reduction）和关联规则学习。"
        "常用算法有 K-Means 聚类、层次聚类、DBSCAN、主成分分析（PCA）和 t-SNE 等。"
        "监督学习和无监督学习的主要区别在于：监督学习需要标注数据，目标是预测标签；"
        "无监督学习不需要标注，目标是发现数据中的隐藏结构。"
    )

    section_title(pdf, "1.2 关键概念")
    body_text(pdf,
        "训练集（Training Set）：用于训练模型的数据子集。模型通过训练集学习特征与标签之间的映射关系。"
        "训练集通常占总数据量的 60%-80%。\n\n"
        "测试集（Test Set）：用于评估模型泛化能力的数据子集。"
        "测试集在整个训练过程中不应被模型「看到」，以保证评估的客观性。\n\n"
        "特征（Feature）：描述样本属性的变量，是模型的输入。"
        "例如在房价预测中，面积、楼层、地段等就是特征。"
        "特征工程（Feature Engineering）是提升模型性能的关键步骤。\n\n"
        "标签（Label）：样本对应的目标值或类别，是监督学习中模型要预测的输出。"
        "标签可以是离散的（分类任务）或连续的（回归任务）。\n\n"
        "验证集（Validation Set）：用于模型选择和超参数调优的数据子集，通常从训练集中划分出一部分。"
    )

    # ════════ 第 3 页：第二章 ════════
    pdf.add_page()
    chapter_title(pdf, "第二章 深度学习入门")

    section_title(pdf, "2.1 神经网络基本结构")
    body_text(pdf,
        "深度学习（Deep Learning）是机器学习的一个子领域，基于人工神经网络（Artificial Neural Network）构建。"
        "神经网络的基本结构由三层组成：输入层（Input Layer）、隐藏层（Hidden Layer）和输出层（Output Layer）。\n\n"
        "输入层负责接收原始数据，每个神经元对应一个输入特征。"
        "例如输入一张 28×28 的灰度图像时，输入层有 784 个神经元，每个神经元接收一个像素值。\n\n"
        "隐藏层位于输入层和输出层之间，负责提取和学习数据中的抽象特征。"
        "一个深度网络可以包含多个隐藏层，每一层从前一层的输出中学习更高层次的特征表示。"
        "第一层可能学习边缘和纹理，中间层学习形状和部件，最后层学习完整的物体概念。"
        "隐藏层的神经元通过权重（Weight）和偏置（Bias）与前后层相连，"
        "每个连接都有一个权重值，表示该连接的重要性。\n\n"
        "输出层产生最终的预测结果。在分类任务中，输出层通常使用 Softmax 函数将原始输出转换为概率分布；"
        "在回归任务中，输出层直接输出一个连续值。"
    )

    section_title(pdf, "2.2 激活函数")
    body_text(pdf,
        "激活函数（Activation Function）为神经网络引入非线性变换能力，没有激活函数的神经网络等价于线性模型，"
        "无法解决复杂的非线性问题。以下是三种常用的激活函数：\n\n"
        "ReLU（Rectified Linear Unit）：定义为 f(x) = max(0, x)。\n"
        "ReLU 的特点是计算简单高效（仅需判断输入是否大于 0），能有效缓解梯度消失问题，在正区间梯度恒为 1。"
        "但由于负区间输出恒为 0，可能导致部分神经元「死亡」（Dead ReLU），即永远不被激活。"
        "为解决此问题，后续出现了 Leaky ReLU、PReLU 等变体。\n\n"
        "Sigmoid 函数：定义为 f(x) = 1 / (1 + e^(-x))，输出范围 (0, 1)。"
        "Sigmoid 将任意实数映射为概率值，适合二分类输出层。"
        "缺点是当输入绝对值较大时，梯度趋近于 0，容易导致梯度消失；且输出不以 0 为中心，影响优化效率。\n\n"
        "Tanh 函数：定义为 f(x) = (e^x - e^(-x)) / (e^x + e^(-x))，输出范围 (-1, 1)。"
        "与 Sigmoid 相比，Tanh 的输出以 0 为中心，优化效果通常更好，但同样存在梯度消失问题。"
    )

    # ════════ 第 4 页：第三章 ════════
    pdf.add_page()
    chapter_title(pdf, "第三章 自然语言处理")

    section_title(pdf, "3.1 Transformer 架构核心机制")
    body_text(pdf,
        "Transformer 是 Vaswani 等人在 2017 年提出的革命性架构，彻底改变了自然语言处理（NLP）领域。"
        "它完全基于注意力机制（Attention Mechanism），摒弃了传统的循环结构（RNN），可以并行处理整个序列。\n\n"
        "Transformer 架构中的自注意力机制（Self-Attention）如何工作：\n"
        "自注意力机制允许模型在处理序列中的每个词时，关注序列中的其他所有词，从而动态计算上下文相关的表示。"
        "具体来说，对于每个输入 token，模型生成三个向量：查询（Query, Q）、键（Key, K）和值（Value, V）。"
        "注意力权重通过 Q 和 K 的点积计算得到，经过缩放和 Softmax 归一化后，再与 V 加权求和。"
        "公式为：Attention(Q, K, V) = softmax(QK^T / √d_k) · V，"
        "其中 √d_k 是缩放因子，防止点积过大导致 Softmax 梯度消失。\n\n"
        "多头注意力（Multi-Head Attention）是自注意力的扩展："
        "将 Q、K、V 分别投影到多个不同的子空间（即「头」），在每个子空间中独立计算注意力，"
        "然后将所有头的输出拼接起来再做一次线性变换。"
        "这种设计使模型能够同时关注来自不同表示子空间的信息，例如一个头关注语法结构，另一个头关注语义关系。\n\n"
        "除了注意力机制，Transformer 还包含位置编码（Positional Encoding）用于注入序列位置信息，"
        "以及前馈网络（Feed-Forward Network）、层归一化（Layer Normalization）和残差连接（Residual Connection）等组件。"
    )

    section_title(pdf, "3.2 BERT 与 GPT 的区别")
    body_text(pdf,
        "BERT（Bidirectional Encoder Representations from Transformers）和 GPT（Generative Pre-trained Transformer）"
        "是两种最具代表性的预训练语言模型，它们在架构和训练目标上有本质区别：\n\n"
        "BERT 使用 Transformer 的编码器（Encoder）部分，是双向模型（Bidirectional），"
        "意味着在处理每个 token 时，BERT 可以同时关注其左侧和右侧的上下文信息。"
        "BERT 的预训练目标包括：掩码语言模型（Masked Language Model, MLM）——随机遮盖部分 token 让模型预测；"
        "和下一句预测（Next Sentence Prediction, NSP）——判断两个句子是否连续。"
        "BERT 擅长理解任务，如文本分类、命名实体识别、问答系统等。\n\n"
        "GPT 使用 Transformer 的解码器（Decoder）部分，是单向（自回归）模型（Autoregressive），"
        "只能从左到右依次预测下一个 token，即只能关注当前位置左侧的上下文。"
        "GPT 的预训练目标就是标准的语言模型：给定前文预测下一个词。"
        "GPT 擅长生成任务，如文本续写、对话生成、代码生成等。\n\n"
        "核心区别总结：BERT 是双向编码器，适合理解；GPT 是单向解码器，适合生成；"
        "BERT 使用 MLM 预训练，GPT 使用自回归语言模型预训练。"
    )

    # ════════ 第 5 页：第四章 ════════
    pdf.add_page()
    chapter_title(pdf, "第四章 计算机视觉")

    section_title(pdf, "4.1 CNN 的核心组件")
    body_text(pdf,
        "卷积神经网络（Convolutional Neural Network, CNN）是计算机视觉领域的基石架构。"
        "CNN 通过三个核心组件实现对图像特征的层次化提取：\n\n"
        "卷积层（Convolutional Layer）是 CNN 的核心。它使用一组可学习的卷积核（Filter/Kernel）"
        "在输入图像上滑动，进行局部区域的点积运算，生成特征图（Feature Map）。"
        "卷积层的作用是自动提取图像的局部特征——浅层卷积提取边缘、纹理等低级特征，"
        "深层卷积提取形状、物体部件等高级语义特征。卷积核的参数共享机制大幅减少了参数量，"
        "使模型具有平移不变性（Translation Invariance），即物体在图像中平移后，卷积层仍能识别它。\n\n"
        "池化层（Pooling Layer）通常紧跟在卷积层之后，用于对特征图进行下采样。"
        "最常见的池化操作是最大池化（Max Pooling）和平均池化（Average Pooling）。"
        "池化层的作用包括：降低特征图的空间尺寸，减少后续层的参数量和计算量；"
        "提供一定程度的平移不变性；扩大感受野（Receptive Field）。\n\n"
        "全连接层（Fully Connected Layer）通常位于 CNN 的末端，将卷积和池化提取到的分布式特征"
        "映射到样本标记空间，用于最终的分类或回归输出。"
        "在全连接层之前，通常需要将多维特征图展平（Flatten）为一维向量。"
    )

    section_title(pdf, "4.2 计算机视觉的典型应用")
    body_text(pdf,
        "图像分类（Image Classification）：判断图像中包含什么类别的物体，如识别一张图片是猫还是狗。"
        "经典模型有 AlexNet、VGG、ResNet、EfficientNet 等。\n\n"
        "目标检测（Object Detection）：不仅识别图像中的物体类别，还要定位每个物体的位置，"
        "通常用边界框（Bounding Box）表示。经典模型有 YOLO 系列、Faster R-CNN、SSD 等。\n\n"
        "图像分割（Image Segmentation）：将图像中的每个像素分配到一个类别，"
        "分为语义分割（Semantic Segmentation，区分不同类别）和实例分割（Instance Segmentation，区分同类的不同个体）。"
        "经典模型有 U-Net、Mask R-CNN、DeepLab 等。"
    )

    # ════════ 第 6 页：第五章 ════════
    pdf.add_page()
    chapter_title(pdf, "第五章 模型评估与调优")

    section_title(pdf, "5.1 过拟合与欠拟合")
    body_text(pdf,
        "过拟合（Overfitting）是指模型在训练数据上表现极好，但在测试数据（未见过的数据）上表现很差。"
        "通俗地说，过拟合就是模型「死记硬背」了训练数据的细节和噪声，而没有学到真正的通用规律。"
        "过拟合的特征包括：训练误差远低于验证误差；模型参数数量远多于训练样本数；模型过于复杂。\n\n"
        "解决过拟合的方法有多种：\n"
        "1. 增加训练数据量——更多的数据可以帮助模型学到更通用的模式；\n"
        "2. 正则化（Regularization）——在损失函数中加入惩罚项限制模型复杂度，L1 正则化产生稀疏权重，L2 正则化（权重衰减）将权重拉向零；\n"
        "3. Dropout——训练时随机丢弃一部分神经元，强制网络学习冗余表示，减少神经元之间的共适应；\n"
        "4. 早停法（Early Stopping）——当验证集误差不再下降时提前结束训练；\n"
        "5. 数据增强（Data Augmentation）——对训练数据进行随机变换（如旋转、翻转、裁剪）来扩充数据集；\n"
        "6. 简化模型——减少网络层数或神经元数量。\n\n"
        "欠拟合（Underfitting）是指模型在训练数据上本身就表现不佳，尚未充分学习数据中的规律。"
        "原因通常是模型过于简单或训练不充分。解决方案包括：增加模型复杂度、延长训练时间、减少正则化强度或改进特征工程。"
    )

    section_title(pdf, "5.2 交叉验证与正则化")
    body_text(pdf,
        "交叉验证（Cross-Validation）是一种评估模型泛化能力的稳健方法。"
        "最常用的是 K 折交叉验证（K-Fold Cross-Validation）：将数据集分为 K 个大小相等的子集，"
        "每次用 K-1 个子集训练，剩下的 1 个子集验证，轮换 K 次，取 K 次验证结果的平均值作为最终评估。"
        "常用 K=5 或 K=10。交叉验证能更充分地利用有限数据，评估结果更加可靠。\n\n"
        "正则化是防止过拟合的核心技术。L1 正则化（Lasso）在损失函数中添加权重绝对值之和作为惩罚项，"
        "倾向于产生稀疏权重矩阵，天然具有特征选择的效果。"
        "L2 正则化（Ridge/权重衰减）在损失函数中添加权重平方和作为惩罚项，"
        "使所有权重均匀缩小但不为零，对异常值更加稳定。\n\n"
        "Dropout 由 Hinton 等人于 2012 年提出，是深度学习中广泛使用的正则化技术。"
        "训练时以概率 p 随机将神经元输出置零，测试时则保留所有神经元但将输出乘以 p 进行缩放。"
        "Dropout 可以理解为每次训练一个不同的子网络，最终测试时相当于对指数级数量的子网络做模型集成（Model Ensemble）。"
    )

    # ════════ 第 7 页：第六章 ════════
    pdf.add_page()
    chapter_title(pdf, "第六章 数据预处理")

    section_title(pdf, "6.1 标准化与归一化")
    body_text(pdf,
        "数据预处理是机器学习流程中至关重要的一步，数据质量直接影响模型性能的上限。\n\n"
        "标准化（Standardization / Z-Score Normalization）将数据变换为均值为 0、标准差为 1 的分布。"
        "公式：x' = (x - μ) / σ，其中 μ 是均值，σ 是标准差。"
        "数据标准化的目的是消除不同特征之间的量纲差异，使各特征对模型的影响在同一尺度上。"
        "这对于依赖于距离度量的算法（如 SVM、KNN、K-Means）和基于梯度优化的算法（如神经网络）尤为重要。"
        "标准化不要求数据服从正态分布，但标准化后数据近似标准正态分布时，梯度下降收敛更快更稳定。\n\n"
        "归一化（Normalization / Min-Max Scaling）将数据线性缩放到 [0, 1] 或 [-1, 1] 区间。"
        "公式：x' = (x - x_min) / (x_max - x_min)。"
        "归一化适用于数据分布边界已知的场景，如图像像素值（0-255）缩放到 (0, 1)。"
        "归一化对异常值敏感，当数据中包含极端值时，大部分数据会被压缩到很小的区间。"
    )

    section_title(pdf, "6.2 缺失值处理与特征工程")
    body_text(pdf,
        "缺失值处理是数据清洗的重要环节。常见策略包括：\n"
        "删除法——直接删除含有缺失值的样本或特征（适用于缺失比例很小的场景）；\n"
        "填补法——用均值、中位数、众数填充数值型缺失值，或用模型预测缺失值；\n"
        "指示变量法——创建二值变量标记缺失位置，让模型自行学习缺失的模式。\n\n"
        "数据增强（Data Augmentation）通过对现有数据进行变换来生成更多训练样本，"
        "是解决数据不足和防止过拟合的有效手段。"
        "在计算机视觉中，常见的数据增强包括随机裁剪、水平翻转、旋转、色彩抖动、高斯噪声等；"
        "在 NLP 中，可以使用同义词替换、回译、随机插入/删除等技巧。\n\n"
        "特征工程（Feature Engineering）是将原始数据转化为模型可有效利用的特征的过程。"
        "好的特征工程的重要性不亚于模型选择——优秀的特征可以让简单模型也能取得良好效果。"
        "特征工程包括特征构造（从原始数据中创建新特征）、特征选择（筛选最有信息量的特征）和特征转换（如对数变换、多项式特征等）。"
    )

    # ════════ 第 8 页：第七章 ════════
    pdf.add_page()
    chapter_title(pdf, "第七章 常见模型架构")

    section_title(pdf, "7.1 RNN、LSTM、GRU 的演进关系")
    body_text(pdf,
        "循环神经网络（Recurrent Neural Network, RNN）是处理序列数据的基础架构。"
        "RNN 的核心思想是引入隐藏状态（Hidden State），将前一时间步的信息循环传递到当前时间步，"
        "使网络具有「记忆」能力。然而，标准 RNN 存在严重的梯度消失问题——"
        "当序列较长时，反向传播中的梯度连乘会导致早期时间步的梯度指数级衰减至接近零，"
        "模型无法学习长距离依赖关系。\n\n"
        "长短期记忆网络（Long Short Memory, LSTM）由 Hochreiter 和 Schmidhuber 于 1997 年提出，"
        "专门用于解决 RNN 的梯度消失问题。"
        "LSTM 通过精巧的门控机制（Gating Mechanism）来控制信息的流动。\n\n"
        "具体来说，LSTM 包含三个门：\n"
        "遗忘门（Forget Gate）——决定从细胞状态中丢弃哪些信息；\n"
        "输入门（Input Gate）——决定将哪些新信息存入细胞状态；\n"
        "输出门（Output Gate）——决定基于细胞状态输出什么信息。\n\n"
        "LSTM 通过这些门控机制维护了一条贯穿所有时间步的「细胞状态」（Cell State）高速公路，"
        "梯度可以沿这条路径几乎无损地传播，从而有效解决了长序列中的梯度消失问题。\n\n"
        "门控循环单元（GRU, Gated Recurrent Unit）是 LSTM 的简化版本，"
        "将遗忘门和输入门合并为「更新门」（Update Gate），同时使用「重置门」（Reset Gate）来控制历史信息的利用程度。"
        "GRU 参数量更少、计算更快，在许多任务上表现与 LSTM 相当。"
    )

    # ════════ 第 9 页：第八章 ════════
    pdf.add_page()
    chapter_title(pdf, "第八章 迁移学习与预训练")

    section_title(pdf, "8.1 迁移学习的核心思想和方法")
    body_text(pdf,
        "迁移学习（Transfer Learning）的核心思想是将在一个任务（源任务）上学习到的知识迁移到另一个相关任务（目标任务）上，"
        "从而在目标任务数据较少的情况下也能训练出高性能模型。"
        "这类似于人类的学习方式——学会骑自行车后，学习骑摩托车会更快。\n\n"
        "迁移学习主要有两种方法：\n\n"
        "第一种是特征提取（Feature Extraction）：将预训练模型作为固定的特征提取器。"
        "具体做法是冻结（Freeze）预训练模型的所有卷积层/编码器层的权重，"
        "只训练最后自己添加的分类层（Classifier Head）。"
        "这种方法在目标任务数据量较少时尤其有效，因为只需训练少量参数，过拟合风险低。\n\n"
        "第二种是微调（Fine-Tuning）：不仅训练新增的分类层，还对预训练模型的部分或全部层进行微调。"
        "通常使用较小的学习率，以避免破坏预训练权重中已经学到的有用特征。"
        "可以只微调最后几层（部分微调），也可以微调整个网络（全量微调）。"
        "微调方法在目标任务数据量较充足时通常能取得比特征提取更好的效果。"
    )

    section_title(pdf, "8.2 预训练模型的使用场景")
    body_text(pdf,
        "预训练模型（Pre-trained Model）是指在大规模通用数据集上预先训练好的模型，"
        "可以直接用于下游任务的迁移学习。"
        "经典的预训练模型包括：计算机视觉领域的 ImageNet 预训练模型（ResNet、VGG、EfficientNet 等）；"
        "NLP 领域的 BERT、GPT、RoBERTa、T5 等。\n\n"
        "预训练模型的使用场景非常广泛：当目标任务训练数据有限时，使用预训练模型可以显著提升性能；"
        "当计算资源有限时，冻结预训练特征提取器能大幅降低训练成本；"
        "在需要快速原型验证时，预训练模型提供了高质量的起点。"
        "现代深度学习实践中，「预训练 + 微调」已成为处理各种任务的标准范式。"
    )

    # ════════ 第 10 页：第九章 ════════
    pdf.add_page()
    chapter_title(pdf, "第九章 AI 伦理与安全")

    section_title(pdf, "9.1 AI 伦理面临的三大核心挑战")
    body_text(pdf,
        "随着人工智能技术的快速发展和广泛应用，AI 伦理问题日益成为学术界、产业界和政策制定者关注的焦点。"
        "AI 伦理面临的三大核心挑战是：数据隐私、算法偏见和可解释性。\n\n"
        "数据隐私（Data Privacy）：AI 系统通常需要海量数据进行训练，这些数据可能包含个人信息、"
        "医疗记录、金融交易记录等敏感信息。如何在利用数据的同时保护个人隐私是核心难题。"
        "相关技术方案包括联邦学习（Federated Learning）——数据不出本地，只交换模型参数；"
        "差分隐私（Differential Privacy）——在查询结果中注入噪声，使攻击者无法推断个体信息；"
        "以及同态加密、安全多方计算等密码学方法。\n\n"
        "算法偏见（Algorithmic Bias）：AI 系统可能从训练数据中学习并放大社会中已有的偏见。"
        "例如，招聘算法可能因历史数据中的性别偏见而歧视女性求职者；"
        "面部识别系统对某些肤色人群的准确率可能显著低于其他人群。"
        "解决算法偏见需要从数据收集、模型设计、评估标准等多方面入手，确保训练数据的多样性和代表性。\n\n"
        "可解释性（Explainability）：深度学习模型通常被视为「黑箱」——"
        "虽然能做出准确预测，但很难解释模型为什么做出某个特定决策。"
        "在医疗诊断、信贷审批、司法判决等高风险领域，可解释性至关重要。"
        "相关技术包括 LIME、SHAP 等局部解释方法，以及注意力可视化等机制。\n\n"
        "负责任 AI（Responsible AI）的基本原则包括：公平（Fairness）、透明（Transparency）、"
        "可问责（Accountability）、隐私保护（Privacy）和安全可靠（Safety & Reliability）。"
        "构建负责任的 AI 系统不仅是技术问题，更需要跨学科合作和健全的法律法规框架。"
    )


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def main():
    """生成测试 PDF。"""
    # 确定输出路径
    project_root = Path(__file__).resolve().parent.parent
    output_dir = project_root / "data" / "knowledge" / "test"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "ai_handbook.pdf"

    print(f"[info] 项目根目录: {project_root}")
    print(f"[info] 输出路径: {output_path}")

    pdf = ChinesePDF()

    if not pdf.font_added:
        print("[ERROR] 未找到中文字体，无法生成含中文的 PDF。")
        print("请将中文字体文件（如 simhei.ttf）放入 C:/Windows/Fonts/ 目录。")
        sys.exit(1)

    generate(pdf)
    pdf.output(str(output_path))
    print(f"[done] PDF 已生成: {output_path}")
    print(f"[done] 共 {pdf.page_no()} 页")


if __name__ == "__main__":
    main()
