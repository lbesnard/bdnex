

def test_a_number_glued_to_the_title_is_the_volume_when_nothing_else_is():
    from bdnex.importer.folders import title_and_volume
    from bdnex.importer.matching import volumes_for

    assert title_and_volume("/x/Sons Of Anarchy - SOA3.pdf") == (["sons", "of", "anarchy", "soa"], 3)
    assert title_and_volume("/x/SonOfAnarchy6.pdf") == (["sonofanarchy"], 6)
    assert title_and_volume("/x/Nains - T02.cbz")[1] == 2  # a marked volume wins
    assert volumes_for("/x/Sons Of Anarchy - SOA3.pdf") == [3]
    assert volumes_for("/x/Nains - T02.cbz") == [2]
