import sys

import torch
from transformers import PreTrainedModel

from style_bert_vits2.constants import Languages
from style_bert_vits2.text_processing import bert_models
from style_bert_vits2.text_processing.japanese.g2p import text_to_sep_kata


__models: dict[torch.device | str, PreTrainedModel] = {}


def extract_bert_feature(
    text: str,
    word2ph: list[int],
    device: torch.device | str,
    assist_text: str | None = None,
    assist_text_weight: float = 0.7,
) -> torch.Tensor:
    """
    日本語のテキストから BERT の特徴量を抽出する

    Args:
        text (str): 日本語のテキスト
        word2ph (list[int]): 元のテキストの各文字に音素が何個割り当てられるかを表すリスト
        device (torch.device | str): 推論に利用するデバイス
        assist_text (str | None, optional): 補助テキスト (デフォルト: None)
        assist_text_weight (float, optional): 補助テキストの重み (デフォルト: 0.7)

    Returns:
        torch.Tensor: BERT の特徴量
    """

    # 各単語が何文字かを作る `word2ph` を使う必要があるので、読めない文字は必ず無視する
    # でないと `word2ph` の結果とテキストの文字数結果が整合性が取れない
    text = "".join(text_to_sep_kata(text, raise_yomi_error=False)[0])

    if assist_text:
        assist_text = "".join(text_to_sep_kata(assist_text, raise_yomi_error=False)[0])
    if (
        sys.platform == "darwin"
        and torch.backends.mps.is_available()
        and device == "cpu"
    ):
        device = "mps"
    if not device:
        device = "cuda"
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    if device not in __models.keys():
        __models[device] = bert_models.load_model(Languages.JP).to(device)  # type: ignore

    style_res_mean = None
    with torch.no_grad():
        tokenizer = bert_models.load_tokenizer(Languages.JP)
        inputs = tokenizer(text, return_tensors="pt")
        for i in inputs:
            inputs[i] = inputs[i].to(device)  # type: ignore
        res = __models[device](**inputs, output_hidden_states=True)
        res = torch.cat(res["hidden_states"][-3:-2], -1)[0].cpu()
        if assist_text:
            style_inputs = tokenizer(assist_text, return_tensors="pt")
            for i in style_inputs:
                style_inputs[i] = style_inputs[i].to(device)  # type: ignore
            style_res = __models[device](**style_inputs, output_hidden_states=True)
            style_res = torch.cat(style_res["hidden_states"][-3:-2], -1)[0].cpu()
            style_res_mean = style_res.mean(0)

    assert len(word2ph) == len(text) + 2, text
    word2phone = word2ph
    phone_level_feature = []
    for i in range(len(word2phone)):
        if assist_text:
            assert style_res_mean is not None
            repeat_feature = (
                res[i].repeat(word2phone[i], 1) * (1 - assist_text_weight)
                + style_res_mean.repeat(word2phone[i], 1) * assist_text_weight
            )
        else:
            repeat_feature = res[i].repeat(word2phone[i], 1)
        phone_level_feature.append(repeat_feature)

    phone_level_feature = torch.cat(phone_level_feature, dim=0)

    return phone_level_feature.T
