from dc_watch.config import AppConfig
from dc_watch.dcinside import build_list_url, extract_image_urls_from_html, extract_posts_from_html


def test_build_list_url_for_minor_gallery() -> None:
    url = build_list_url(AppConfig(gallery_id="thesingularity", gallery_type="minor"), page=2)

    assert url == "https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity&page=2"


def test_extract_posts_from_mock_list_html() -> None:
    html = """
    <table>
      <tr class="ub-content us-post" data-no="123">
        <td class="gall_num">123</td>
        <td class="gall_tit ub-word"><a href="/mgallery/board/view/?id=thesingularity&no=123">첫 글</a></td>
        <td class="gall_writer ub-writer" data-nick="alice"></td>
      </tr>
      <tr class="ub-content us-post" data-no="notice">
        <td class="gall_num">공지</td>
        <td class="gall_tit ub-word"><a href="/notice">공지</a></td>
      </tr>
    </table>
    """

    posts = extract_posts_from_html(html, "https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity")

    assert len(posts) == 1
    assert posts[0].post_no == "123"
    assert posts[0].title == "첫 글"
    assert posts[0].writer == "alice"
    assert posts[0].url == "https://gall.dcinside.com/mgallery/board/view/?id=thesingularity&no=123"


def test_extract_posts_skips_notices_and_marks_image_posts() -> None:
    html = """
    <table>
      <tr class="ub-content us-post" data-no="900" data-type="icon_notice">
        <td class="gall_num">900</td>
        <td class="gall_subject"><b>공지</b></td>
        <td class="gall_tit ub-word"><a href="/mgallery/board/view/?id=thesingularity&no=900"><em class="icon_img icon_notice"></em>공지</a></td>
      </tr>
      <tr class="ub-content us-post" data-no="901" data-type="icon_txt">
        <td class="gall_num">901</td>
        <td class="gall_subject">일반</td>
        <td class="gall_tit ub-word"><a href="/mgallery/board/view/?id=thesingularity&no=901"><em class="icon_img icon_txt"></em>텍스트글</a></td>
      </tr>
      <tr class="ub-content us-post" data-no="902" data-type="icon_pic">
        <td class="gall_num">902</td>
        <td class="gall_subject">일반</td>
        <td class="gall_tit ub-word"><a href="/mgallery/board/view/?id=thesingularity&no=902"><em class="icon_img icon_pic"></em>이미지글</a></td>
      </tr>
    </table>
    """

    posts = extract_posts_from_html(html, "https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity")

    assert [post.post_no for post in posts] == ["901", "902"]
    assert posts[0].has_image is False
    assert posts[1].has_image is True


def test_extract_image_urls_from_mock_post_html_filters_ui_images() -> None:
    html = """
    <html><body>
      <div class="appending_file_box"><img src="https://dcimg.example.test/content-1.jpg"></div>
      <div class="write_div">
        <img src="//dcimg.example.test/content-2.png">
        <img src="/_images/profile.png">
        <img src="https://ad.example.test/banner.jpg">
      </div>
      <img class="written_dccon" src="https://dcimg.example.test/sticker.gif">
      <a href="https://gall.dcinside.com/board/viewimage/?id=thesingularity&no=123&f_no=abc">첨부</a>
    </body></html>
    """

    urls = extract_image_urls_from_html(
        html,
        "https://gall.dcinside.com/mgallery/board/view/?id=thesingularity&no=123",
    )

    assert "https://dcimg.example.test/content-1.jpg" in urls
    assert "https://dcimg.example.test/content-2.png" in urls
    assert "https://gall.dcinside.com/board/viewimage/?id=thesingularity&no=123&f_no=abc" in urls
    assert all("profile" not in url for url in urls)
    assert all("banner" not in url for url in urls)


def test_extract_image_urls_from_dcinside_attachment_links_filters_small_ui_assets() -> None:
    html = """
    <html><body>
      <div class="writing_view_box">
        <img src="https://nstatic.dcinside.com/dc/w/images/loading_btntype.gif">
        <img src="https://dcimg8.dcinside.co.kr/viewimage.php?id=thesingularity&no=24b0d121e09c28a8699fe8b115ef0464d28cebcee4">
        <img src="https://dcimg8.dcinside.co.kr/viewimage.php?id=thesingularity&no=39b8c332b49c28a8699fe8b115ef046eaf2a7988169e">
        <img class="kcaptcha" src="https://nstatic.dcinside.com/dc/w/images/kcap_none.png">
      </div>
      <ul class="appending_file">
        <li><a href="https://image.dcinside.com/viewimagePop.php?id=thesingularity&no=24b0d121e09c28a8699fe8b115ef0464d28cebcee4">image.png</a></li>
        <li><a href="https://image.dcinside.com/viewimagePop.php?id=thesingularity&no=39b8c332b49c28a8699fe8b115ef046eaf2a7988169e">test1.png</a></li>
      </ul>
      <img src="https://gall.dcinside.com/board/comment_box.gif">
      <img src="https://gall.dcinside.com/_images/profile.png">
    </body></html>
    """

    urls = extract_image_urls_from_html(
        html,
        "https://gall.dcinside.com/mgallery/board/view/?id=thesingularity&no=1230835&page=1",
    )

    assert "https://dcimg8.dcinside.co.kr/viewimage.php?id=thesingularity&no=24b0d121e09c28a8699fe8b115ef0464d28cebcee4" in urls
    assert "https://dcimg8.dcinside.co.kr/viewimage.php?id=thesingularity&no=39b8c332b49c28a8699fe8b115ef046eaf2a7988169e" in urls
    assert all("nstatic.dcinside.com" not in url for url in urls)
    assert all("comment_box" not in url for url in urls)
