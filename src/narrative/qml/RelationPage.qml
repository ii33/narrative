import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 人物关系图：人是点，关系是有方向的边。
// 一条关系就是一条属性记录——属性名是关系性质，值指向对方——所以两个人之间
// 可以有两支箭头（师父 / 徒弟），也可以只有一支（崇拜）。
Item {
    id: page

    property var app
    property var persons: []
    property var edges: []
    property string notice: ""

    readonly property var t: app ? app.theme : ({})
    readonly property string ink: t && t.ink ? t.ink : "#16233a"
    readonly property string muted: t && t.muted ? t.muted : "#6b7a90"
    readonly property string line: t && t.line ? t.line : "#dfe6ef"
    readonly property string accent: t && t.accent ? t.accent : "#2b6cb0"

    function load() {
        page.notice = ""
        bridge.loadRelations()
    }

    function apply(payload) {
        persons = payload.persons || []
        edges = payload.edges || []
        canvas.requestPaint()
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind !== "relations") return
            page.apply(payload)
        }
        function onErrorOccurred(kind, message) {
            if (kind === "relations") page.notice = message
        }
    }

    // 切到这一页才去问：启动时还没有打开工程，问了只会报错。
    // 切到这一页才去问：启动时还没有打开工程，问了只会报错。
    onVisibleChanged: if (visible) load()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Label {
                text: "人物关系图"
                color: page.ink
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Label {
                text: page.persons.length + " 人 · " + page.edges.length + " 条关系"
                color: page.muted
                font.pixelSize: 12
            }
            Item { Layout.fillWidth: true }
            Label {
                visible: page.notice.length > 0
                text: page.notice
                color: page.muted
                font.pixelSize: 11
            }
            Button {
                text: "刷新"
                flat: true
                onClicked: page.load()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: page.line
        }

        // 一张图画完：圆环排开人物，边上写关系性质，括号里是状态。
        Canvas {
            id: canvas
            objectName: "relationCanvas"
            Layout.fillWidth: true
            Layout.fillHeight: true

            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()

            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                var people = page.persons
                var links = page.edges
                if (people.length === 0) {
                    ctx.fillStyle = page.muted
                    ctx.font = "13px sans-serif"
                    ctx.fillText("还没有人物节点", 20, 30)
                    return
                }
                var cx = width / 2
                var cy = height / 2
                var radius = Math.max(80, Math.min(width, height) * 0.34)
                var pos = ({})
                for (var i = 0; i < people.length; ++i) {
                    var angle = -Math.PI / 2 + i * 2 * Math.PI / people.length
                    pos[people[i].id] = {
                        x: cx + radius * Math.cos(angle),
                        y: cy + radius * Math.sin(angle)
                    }
                }

                ctx.lineWidth = 1.2
                ctx.strokeStyle = page.accent
                ctx.fillStyle = page.muted
                ctx.font = "11px sans-serif"

                for (var e = 0; e < links.length; ++e) {
                    var edge = links[e]
                    var a = pos[edge.from]
                    var b = pos[edge.to]
                    if (!a || !b) continue
                    var dx = b.x - a.x
                    var dy = b.y - a.y
                    var len = Math.sqrt(dx * dx + dy * dy) || 1
                    var ux = dx / len
                    var uy = dy / len
                    var pad = 26
                    var x1 = a.x + ux * pad
                    var y1 = a.y + uy * pad
                    var x2 = b.x - ux * pad
                    var y2 = b.y - uy * pad
                    // 双向的两条边错开一点，不然重叠成一条。
                    var back = links.some(function (other) {
                        return other.from === edge.to && other.to === edge.from
                    })
                    var shift = back ? 5 : 0
                    x1 -= uy * shift; y1 += ux * shift
                    x2 -= uy * shift; y2 += ux * shift

                    ctx.beginPath()
                    ctx.moveTo(x1, y1)
                    ctx.lineTo(x2, y2)
                    ctx.stroke()

                    var head = 7
                    ctx.beginPath()
                    ctx.moveTo(x2, y2)
                    ctx.lineTo(x2 - ux * head - uy * head * 0.6,
                               y2 - uy * head + ux * head * 0.6)
                    ctx.lineTo(x2 - ux * head + uy * head * 0.6,
                               y2 - uy * head - ux * head * 0.6)
                    ctx.closePath()
                    ctx.fill()

                    var label = edge.kind + (edge.state ? "（" + edge.state + "）" : "")
                    ctx.fillText(label, (x1 + x2) / 2 + 4, (y1 + y2) / 2 - 4)
                }

                for (var p = 0; p < people.length; ++p) {
                    var person = people[p]
                    var spot = pos[person.id]
                    ctx.beginPath()
                    ctx.arc(spot.x, spot.y, 20, 0, 2 * Math.PI)
                    ctx.fillStyle = "#ffffff"
                    ctx.fill()
                    ctx.strokeStyle = page.accent
                    ctx.lineWidth = 1
                    ctx.stroke()
                    ctx.fillStyle = page.ink
                    ctx.font = "12px sans-serif"
                    ctx.textAlign = "center"
                    ctx.fillText(person.name, spot.x, spot.y + 4)
                    ctx.textAlign = "left"
                }
            }
        }

        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: "箭头方向就是关系方向：师父 / 徒弟是同一件事的两侧，各一条；"
                + "崇拜、仇敌这类只有一侧。括号里是关系当前的状态。"
            color: page.muted
            font.pixelSize: 11
        }
    }
}
