import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Rectangle{
 id:compare;property var desk;property bool overlay:false;color:"#202725"
 readonly property string sketch:desk.frame?.sketch_path||""
 readonly property string style:desk.frame?.style_path||""
 StoryboardAnimatedImage{desk:compare.desk;visible:compare.overlay;anchors.fill:parent;source:bridge&&compare.sketch?bridge.storyboardImageUrl(compare.sketch):"";fillMode:Image.PreserveAspectFit}
 StoryboardAnimatedImage{desk:compare.desk;visible:compare.overlay&&!!compare.style;anchors.fill:parent;source:bridge&&compare.style?bridge.storyboardImageUrl(compare.style):"";fillMode:Image.PreserveAspectFit;opacity:blend.value}
 RowLayout{visible:!compare.overlay;anchors.fill:parent;spacing:2
  Item{Layout.fillWidth:true;Layout.fillHeight:true;StoryboardAnimatedImage{desk:compare.desk;anchors.fill:parent;source:bridge&&compare.sketch?bridge.storyboardImageUrl(compare.sketch):"";fillMode:Image.PreserveAspectFit}Label{text:"素描";color:"white";padding:4}}
  Item{Layout.fillWidth:true;Layout.fillHeight:true;StoryboardAnimatedImage{desk:compare.desk;anchors.fill:parent;source:bridge&&(compare.style||compare.sketch)?bridge.storyboardImageUrl(compare.style||compare.sketch):"";fillMode:Image.PreserveAspectFit}Label{text:compare.style?"风格图":"风格图缺失，沿用素描";color:"white";padding:4}}
 }
 Slider{id:blend;objectName:"storyboardCompareOpacity";visible:compare.overlay;anchors.left:parent.left;anchors.right:parent.right;anchors.bottom:parent.bottom;from:0;to:1;value:0.5}
 Label{visible:compare.overlay&&!compare.style;text:"风格图缺失，当前仅显示素描";color:"white";padding:4}
}
