from desktop import selfcheck
from desktop.fake import FakeService
from desktop.service import DesktopService


def test_fake_run_makes_doc_reads_and_exports(tmp_path, capsys):
    code = selfcheck.run(FakeService(delay=0), tmp_path)
    out = capsys.readouterr().out
    assert code == 1                                  # 연습용 백엔드에는 일부러 실패하는 문서가 있다
    assert "깨진문서" in out and "GPU 메모리 부족" in out
    assert (tmp_path / "만든 문서" / "selfcheck_store.pdf").exists()
    assert (tmp_path / "확정.xlsx").exists()          # 실패한 문서가 있어도 읽은 문서는 내보낸다
    assert "실패 1건" in out


def test_missing_models_stops_before_reading(tmp_path, capsys):
    code = selfcheck.run(DesktopService(models_dir=tmp_path / "없는폴더"), tmp_path)
    out = capsys.readouterr().out
    assert code == 1 and "모델 파일이 없습니다" in out
    assert not (tmp_path / "읽은 결과").exists()
