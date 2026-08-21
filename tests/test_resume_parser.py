from resume_parser import ResumeParser


def _parser_stub() -> ResumeParser:
    parser = ResumeParser.__new__(ResumeParser)
    parser.all_tags = {
        "Python", "PyTorch", "LLM", "Agent", "Java", "汽车维修", "PhotoShop", "开发",
    }
    parser.tag_vocab = sorted(
        [t for t in parser.all_tags if t != "开发"],
        key=len,
        reverse=True,
    )
    parser.level3_to_tags = {
        ".NET": ["Java", "PhotoShop"],
        "4S店管理": ["汽车维修"],
        "AI Agent工程师": ["Agent", "LLM", "Python"],
        "算法工程师": ["Python", "PyTorch"],
    }
    parser.level3_list = list(parser.level3_to_tags)
    return parser


def test_candidate_tags_come_from_resume_not_csv_head():
    parser = _parser_stub()
    text = "熟悉 Python、PyTorch，做过 LLM Agent 项目。"
    selected = parser._rank_level3_for_resume(text)[:10]
    tags = parser._build_candidate_tags(text, selected)
    assert "Python" in tags
    assert "PyTorch" in tags
    assert "LLM" in tags
    assert "Agent" in tags
    assert "汽车维修" not in tags
    assert "PhotoShop" not in tags
    assert "开发" not in tags


def test_rank_level3_prefers_matching_families():
    parser = _parser_stub()
    ranked = parser._rank_level3_for_resume("Python LLM Agent 开发")
    assert ranked[0] in {"AI Agent工程师", "算法工程师"}
    assert ranked[0] != ".NET"
    assert "4S店管理" not in ranked
