import QtQuick
import QtQuick.Controls

Rectangle {
    id: axisView
    property var axisData: ({points:[], connections:[], layout_connections:[]})
    property var points: []
    signal pointSelected(string eventId)
    readonly property int maxOrdinal: Math.max(0, (axisData.points || []).reduce(function(maximum, point) { return Math.max(maximum, point.ordinal) }, 0))
    readonly property int rows: Math.max(2, points.reduce(function(maximum, point, index) { return Math.max(maximum, axisView.pointRow(index) + 1) }, 0))
    implicitHeight: 148 + rows * 74
    color: "#ffffff"; radius: 12; border.color: "#d7e0ec"
    clip: true

    function hasOrdinal(ordinal) { return points.some(function(point) { return point.ordinal === ordinal }) }
    function pointRow(index) {
        var row = index % 2
        for (var i = 0; i < index; ++i)
            if (points[i].ordinal === points[index].ordinal) row = Math.max(row, pointRow(i) + 1)
        return row
    }
    function refKey(ref) { return [ref.timeline_id, ref.form, ref.event_id, ref.boundary, ref.number, ref.datetime].join("|") }
    function pointFor(ref) {
        for (var i = 0; i < points.length; ++i) if (refKey(points[i].ref) === refKey(ref)) return points[i]
        return null
    }
    function pointIndex(ref) {
        for (var i = 0; i < points.length; ++i) if (refKey(points[i].ref) === refKey(ref)) return i
        return -1
    }
    function pointX(point) { return 84 + point.ordinal * axisContent.step }
    onPointsChanged: connectors.requestPaint()
    onAxisDataChanged: connectors.requestPaint()

    Flickable {
        id: scroller
        anchors.fill: parent; anchors.margins: 8
        contentWidth: axisContent.width; contentHeight: height
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        ScrollBar.horizontal: ScrollBar { policy: scroller.contentWidth > scroller.width ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff }
        Item {
            id: axisContent
            property real step: Math.max(154, (scroller.width - 168) / Math.max(1,axisView.maxOrdinal))
            width: Math.max(scroller.width, 168 + axisView.maxOrdinal * step)
            height: scroller.height
            Canvas {
                id: connectors
                width: parent.width; height: parent.height
                onWidthChanged: requestPaint()
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.clearRect(0,0,width,height)
                    var rows = axisView.axisData.connections || []
                    for (var i = 0; i < rows.length; ++i) {
                        var edge = rows[i]
                        var left = axisView.pointFor(edge.from_ref), right = axisView.pointFor(edge.to_ref)
                        if (!left || !right) continue
                        var x1 = axisView.pointX(left), x2 = axisView.pointX(right)
                        var top = 22 + (i % 3) * 16
                        ctx.strokeStyle = "#8657b6"; ctx.lineWidth = 2
                        ctx.beginPath(); ctx.moveTo(x1,88); ctx.lineTo(x1,top); ctx.lineTo(x2,top); ctx.lineTo(x2,80); ctx.stroke()
                        ctx.fillStyle = "#8657b6"
                        if (edge.relation !== "same") {
                            ctx.beginPath(); ctx.moveTo(x2,88); ctx.lineTo(x2-4,80); ctx.lineTo(x2+4,80); ctx.closePath(); ctx.fill()
                        }
                        ctx.font = "11px Microsoft YaHei"; ctx.fillText(edge.relation === "same" ? "同时" : "早于", (x1+x2)/2-12, top-4)
                    }
                    var layouts = axisView.axisData.layout_connections || []
                    ctx.strokeStyle = "#a7b2c2"; ctx.lineWidth = 1
                    ctx.setLineDash([3,4])
                    for (var j = 0; j < layouts.length; ++j) {
                        var a = axisView.pointFor(layouts[j].from_ref), b = axisView.pointFor(layouts[j].to_ref)
                        if (!a || !b) continue
                        var xa = axisView.pointX(a), xb = axisView.pointX(b)
                        ctx.beginPath()
                        if (xa === xb) {
                            var ya = 152 + axisView.pointRow(axisView.pointIndex(layouts[j].from_ref))*74
                            var yb = 152 + axisView.pointRow(axisView.pointIndex(layouts[j].to_ref))*74
                            ctx.moveTo(xa+70,ya); ctx.lineTo(xa+77,ya); ctx.lineTo(xa+77,yb); ctx.lineTo(xa+70,yb)
                        } else { ctx.moveTo(xa,99); ctx.lineTo(xb,99) }
                        ctx.stroke()
                    }
                    ctx.setLineDash([])
                }
            }
            Rectangle { x: 55; y: 90; width: parent.width-110; height: 2; color: "#516a91" }
            Text { x: parent.width-55; y: 78; text: "▶"; color: "#516a91"; font.pixelSize: 18 }
            Repeater {
                model: axisView.maxOrdinal + 1
                delegate: Item {
                    required property int index
                    x: 84 + index * axisContent.step; y: 85
                    objectName: "relativeTick_" + axisView.axisData.id + "_" + index
                    z: 3
                    Rectangle { width: 1; height: 12; color: "#516a91" }
                    Rectangle { visible: axisView.hasOrdinal(parent.index); x: -4; y: 2; width: 9; height: 9; radius: 5; color: "#8657b6" }
                    Label { x: -12; y: 12; width: 24; text: String(index); background: Rectangle { color: "#ffffff" } horizontalAlignment: Text.AlignHCenter; font.bold: true; color: "#233b60" }
                }
            }
            Repeater {
                model: axisView.points
                delegate: Item {
                    id: eventPoint
                    required property var modelData
                    required property int index
                    property int row: axisView.pointRow(index)
                    x: axisView.pointX(modelData); y: 90
                    objectName: "relativePoint_" + axisView.axisData.id + "_" + index
                    Rectangle { y: 7; width: 1; height: 22 + eventPoint.row * 74; color: "#c3afda" }
                    Rectangle {
                        objectName: "relativeCard_" + axisView.axisData.id + "_" + eventPoint.index
                        x: -68; y: 30 + eventPoint.row * 74; width: 138; height: 65; radius: 8
                        color: pointMouse.containsMouse ? "#e9dcf5" : "#f2ecf9"; border.color: "#d8c8e7"
                        Label {
                            anchors.fill: parent; anchors.margins: 7
                            text: eventPoint.modelData.event_name + " · " + ({start:"开始",end:"结束",point:"时点"}[eventPoint.modelData.boundary] || "整体")
                            color: "#213957"; font.pixelSize: 12; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
                        }
                        MouseArea { id: pointMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: axisView.pointSelected(eventPoint.modelData.event_id) }
                        ToolTip.visible: pointMouse.containsMouse
                        ToolTip.text: eventPoint.modelData.event_name + " · " + eventPoint.modelData.boundary
                    }
                }
            }
        }
    }
}

