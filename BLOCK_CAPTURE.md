# 江湾相机双块采录

涂布线经过拍位后，上一片的后块可能还没到读窗。双块模式让下一次曝光可以接着用同一槽，同时保住尚待实收的原块。已有的登记、整片质检、清单和离线领件见 README.md。

## 设备和容量

以 `camera.ports.RecordingCamera(pages=PageRig(block_size))` 启用双块模式，其中 `PageRig` 位于 `camera.page_rig`。该模式使用默认的 `left` 和 `right` 两槽，两个块位依原序编号 `0`、`1`。每块含 `block_size` 个仪器整数读值，工单所在班次的点数必须等于 `2 * block_size`。双块模式派拍时拒绝点数不符的规格，抛 `ValueError` 且不占页。无 `pages` 参数时继续使用原整片模式。

五页夹具只存定长原码。`left:0`、`left:1`、`right:0`、`right:1` 各承载对应槽和块位，`spare` 可承载任意一块。设备可在一次事件内搬页。设备只在这五页内保全未实收原码，不能将原码提前复制到站端或存入其他设备缓冲来扩大容量。设备可另存指令、出处和页位等元数据。夹具 `read(page)` 返回原序元组，空页返回 `None`。`write(page, readings)` 写空页，`erase(page)` 清页，`move(source, destination)` 将占用页移入空页。夹具操作由设备侧使用，站端通过下面的读窗接件。

沿用 `DispatchEvent(order_id, target, instruction_id)`。每槽同时至多一笔在途实写。设备接拍时必须有办法同时保全所有仍待取的已写原块和兑现包括本次在内的所有实写承诺。够页就接拍，哪怕旧片尚欠一块。空间暂时不够时，设备的 `advance` 返回 `None`，站端的 `advance` 返回原 `WAITING` 工单，设备未登记该指令，之后可原样再派。已受理指令沿用原任次和原参数核对。两个槽共用的备用页不得分给两个彼此冲突的承诺。

沿用 `WriteEvent(instruction_id, readings, source)` 实写，`readings` 是两块依次相连的整数元组。坏长度或非整数抛 `ValueError`，此时指令仍在途，可重交有效事件或用 `WriteEvent(instruction_id, None)` 明确未写成。已受理的首次合法实写必须能兑现。实际终局和独立的 `NoticeEvent` 重放保留原语义。通知可先到，只记录通知事实。撤拍丢弃该单未签的实收与待取资格。若设备指令仍在途，保留实写承诺直到实写或未写成终局，之后可归还属于该单的页位。迟到或重复的终局不得影响其他曝光。

## 读窗和归档

`camera.page_rig.ReadBlockEvent(instruction_id, block)` 表示一个可用读窗。站端 `ShiftCaptureSession.advance(event)` 推进这一个读窗。相机 `RecordingCamera.advance(event)` 按指定指令与块位提供 `BlockReading(origin, block, readings)`，没有可取原块时返回 `None`。`origin` 沿用 `camera.banks.FrameOrigin`，含原物理槽、实际指令、任次和实写出处。提供原块本身不解除保管。站端验收原件后才反馈该块可归还。页位选择、响应后的归还协议和内部表示可自行设计。

站端首次验收返回 `received`，同一未撤拍任次的已收块重投返回 `already`，已知指令未写成、仍在途或已撤拍且无可接纳原块时返回 `not_ready`。两个块可按任意顺序实收。未知指令抛 `KeyError`，块号必须是整数 0 或 1，否则抛 `ValueError`。来件出处、块位或块形不符抛 `FrameProvenanceError`，保留原有实收和占页。已收重投可在签发后沿用 `already`，不得再次归还设备页。双块模式的 `read_current` 拒绝整片读取，抛 `ValueError`。写入、通知和签存均不替站端打开读窗。

`publish(order_id)` 在两块实际收齐之前抛 `CaptureNotReady`。两块齐时按原序交给原整片签发，沿用班次阈值、结果、首执及交班清单格式。提交失败抛 `PublicationError`，当前会话持有已收原块，可直接续签。先写新片或复用同槽不改变已收原块。停源后通过 `ShiftCaptureSession(path).get(result_id)` 领取同一 `ArchivedCapture`，含原序、实际任次、出处、极差、结论和首执。普通入口、已归档数据和原回归继续有效。设备及未签进度属于当前活跃会话，串行推进。

## 录制与核验

`block_recording.py` 提供录制及预期。左槽 A 写 `(11, 14)`，先收块 0，再让 B 写 `(20, 24)`。右槽 R 也只欠后块时，B 接拍以后 C 要等，即使 B 尚未实写。先收 A 或 B 的后块都能使 C 续拍，另一块仍要凭原指令取回。阈值 3 时 A 合格，B 须复查。提交失败后的 B 可在左槽再次写入后续签，停源领回原片。

`python3 -B block_recording.py fixture` 核对裸页夹具。完成双块采录后，`python3 -B block_recording.py verify` 重放交错、错绑、撤拍终局及提交失败场景，断言失败会退出非零。`python3 -B block_recording.py demo` 展示停源领回的 A 和 B。

交付沿用 `.pointwise-runtime.json` 的 CLI 清单。清单版本为 `1`，`kind` 为 `cli`，`setup` 是准备命令的 argv 列表，`verify` 和 `demo` 各为一条 argv。核验入口运行原套件及双块录制的业务断言，演示入口展示交错取块后停源领取。现有普通录制仍可独立重放。
