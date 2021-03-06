import React from 'react'

import ContentEditorLinkEditor from './LinkEditor'

const LINK_INPUT_WIDTH = 334

export default function LinkPopover(props) {

  const style = {width: LINK_INPUT_WIDTH}

  return (
    <div
      className='c-dispatch-editor__popover'
      onMouseUp={e => e.stopPropagation()}
      style={style}>
      <ContentEditorLinkEditor
        url={props.url}
        close={props.close}
        selection={props.selection}
        insertLink={props.insertLink}
        removeLink={props.removeLink} />
    </div>
  )

}
