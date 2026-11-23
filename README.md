# 江湾带材相机班次采录

江湾涂布厂试制车间用录制相机采集带材试段。班长依据工艺卡开班，采录员按单派拍，质检员从原序整数读值复算极差，交班员依清单和首执在设备停源后核对原片。

## 运行

使用 Python 3.11 标准库和文件 SQLite。在项目根目录运行。

```sh
python3 -B check_project.py
python3 -B demo.py --temporary
```

第一条命令由控制台提供入口并运行 `tests` 下的完整回归，再运行双块录制的业务断言。也可直接运行 `python3 -B -m unittest discover -s tests -t .`。演示重放 `recording.json`，在临时库中运行并清理。传 `--database PATH` 可在一个尚不存在的路径保留演示档案。相同初始数据和录制事件得到相同的档案号、首执和业务结果。

双块模式见 `BLOCK_CAPTURE.md`：以 `RecordingCamera(pages=PageRig(block_size))` 启用，`python3 -B block_recording.py verify` 核验交错读窗、错绑、撤拍与提交失败，`python3 -B block_recording.py demo` 展示停源领取。

## 一班的工作

1. 用 `open(ShiftSpec(班次号, 修订, 点数, 阈值))` 固定工艺卡。点数是正整数，阈值是非负整数。
2. 用 `request(OrderRequest(班次号, 工单号, 试段))` 登记试段。规格和工单的同参重放沿用原记录，异参拒绝。班次规格在第一次成功登记后固定。
3. 用 `advance(DispatchEvent(工单号, 目标, 指令号))` 派拍。返回 `CaptureAttempt`。有效的同槽竞争保持 `WAITING`，目标可用后可再次推进派发。不同物理目标独立工作。
4. 用 `advance(WriteEvent(指令号, 原序读值元组, 出处))` 推进实写。用 `WriteEvent(指令号, None)` 记录设备明确未写成。用 `advance(NoticeEvent(指令号))` 独立记录通知。通知可以先到。设备保留首次实际结局，重复事件不重写槽，既有终局与不同类别的结局冲突时抛 `ValueError`。`cancel(工单号)` 撤拍或返回该单已有的正式成果。撤拍后的在途指令仍可实写，实际结清前其物理目标继续受保护。有效工单未写成时可按原登记再次派拍，使用新的指令号。
5. 已写原片在有效通知后交出完整副本。`publish(工单号)` 也可直接交接已实际结清的原片。交出的副本绑定实际任次、产片指令、物理目标、出处和冻结规格，可在活槽复用后继续用于签存。
6. `publish` 在同一个 SQLite 事务保存原片、正式质控结果和首执。每项读值都是仪器整数刻度，顺序原样保留。极差是最大值减最小值，等于阈值仍合格，超限仍出成果并标为需复查。相同已签任次沿用原成果号和首执。
7. `list(班次号)` 按工单登记顺序列出正式成果，`review_items` 给出需复查件。待拍、撤拍、拒收和签存待续工单没有正式条目。
8. 关闭活跃会话后，可用同一个数据库路径创建无相机的会话，通过 `get(成果号)` 领取已保存的读值、出处、极差、结论和首执。已知空班有空清单，未知班次或成果号返回 `None`。

```python
from camera.events import DispatchEvent, NoticeEvent, WriteEvent
from camera.ports import RecordingCamera
from capture.service import ShiftCaptureSession
from shift.requests import OrderRequest
from shift.spec import ShiftSpec

with ShiftCaptureSession('shift.sqlite', RecordingCamera(('left', 'right'))) as station:
    station.open(ShiftSpec('day', 'R1', 2, 3))
    station.request(OrderRequest('day', 'B', 'strip-02'))
    station.advance(DispatchEvent('B', 'left', 'write-B'))
    station.advance(WriteEvent('write-B', (11, 14), 'recording/002'))
    station.advance(NoticeEvent('write-B'))
    result = station.publish('B')
    print(result.quality.spread, station.list('day').review_items)

with ShiftCaptureSession('shift.sqlite') as archive:
    assert archive.get(result.result_id) == result
```

## 错误与数据保留

非法规格或同号异参抛 `ValueError`。未知班次的工单、未知指令和未知撤拍工单抛 `KeyError`。尚无有效原片时签存抛 `CaptureNotReady`。活槽出处与实际任次冲突时抛 `FrameProvenanceError`。空片、点数不符、含空值或非整数的原片抛 `FrameShapeError`，该任次无正式成果。数据库签存失败抛 `PublicationError`，读端看不到新成果或新首执，当前活跃会话可用原交接副本续办。重复建库仅补齐缺失的结构。已登记规格、工单和正式档案会保留。

设备及未正式签发的采录进度归当前活跃会话。数据库中的规格、登记和正式档案可重开。一个相机和档案库由一个活跃采录会话串行推进，交班读取使用独立连接。无相机会话可以读取班次和档案及重放已完成的签存查询。录制演示只使用本地数据。
