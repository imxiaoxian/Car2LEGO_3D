from scripts.verify_studio_save import root_steps


def test_saved_model_ignores_inline_library_and_decodes_color_numbers():
    part="1 11 0 0 0 1 0 0 0 1 0 0 0 1 3024.dat"
    text=f"0 FILE main.io\n{part}\n0 STEP\n0 NOFILE\n0 FILE 3024.dat\n{part}\n"
    result=root_steps(text,{11:0})
    assert len(result)==1 and sum(result[0].values())==1
    assert next(iter(result[0]))[:2] == ("3024.dat",0)


def test_saved_steps_detect_membership_change_even_with_equal_part_counts():
    part="1 0 0 0 0 1 0 0 0 1 0 0 0 1 3024.dat"
    assert root_steps(f"{part}\n0 STEP\n{part}\n0 STEP") != root_steps(f"{part}\n{part}\n0 STEP")
