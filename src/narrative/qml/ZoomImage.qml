import QtQuick

Item {
    id: viewer
    objectName: "zoomImageViewer"
    clip: true
    property url source
    property real zoom: 1
    property real panX: 0
    property real panY: 0
    readonly property real minZoom: 1
    readonly property real maxZoom: 16

    function resetView() {
        zoom = 1
        panX = 0
        panY = 0
    }

    function zoomAt(x, y, factor) {
        var next = Math.max(minZoom, Math.min(maxZoom, zoom * factor))
        var ratio = next / zoom
        panX = x - width / 2 - (x - width / 2 - panX) * ratio
        panY = y - height / 2 - (y - height / 2 - panY) * ratio
        zoom = next
        if (zoom === minZoom) {
            panX = 0
            panY = 0
        }
    }

    function panBy(dx, dy) {
        panX += dx
        panY += dy
    }

    onSourceChanged: resetView()
    onWidthChanged: resetView()
    onHeightChanged: resetView()

    Image {
        id: image
        objectName: "zoomImageContent"
        width: viewer.width
        height: viewer.height
        x: viewer.panX
        y: viewer.panY
        source: viewer.source
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        scale: viewer.zoom
        transformOrigin: Item.Center
    }

    MouseArea {
        anchors.fill: parent
        cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
        property real lastX: 0
        property real lastY: 0
        onPressed: function(mouse) {
            lastX = mouse.x
            lastY = mouse.y
        }
        onPositionChanged: function(mouse) {
            if (!pressed) return
            viewer.panBy(mouse.x - lastX, mouse.y - lastY)
            lastX = mouse.x
            lastY = mouse.y
        }
        onDoubleClicked: viewer.resetView()
        onWheel: function(wheel) {
            var delta = wheel.angleDelta.y || wheel.pixelDelta.y
            viewer.zoomAt(wheel.x, wheel.y, Math.pow(1.2, delta / 120))
            wheel.accepted = true
        }
    }
}
