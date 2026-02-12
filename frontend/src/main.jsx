import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'

const container = document.getElementById('root') || document.createElement('div')
if(!container.parentElement){ document.body.appendChild(container) }
const root = createRoot(container)
root.render(<App />)
