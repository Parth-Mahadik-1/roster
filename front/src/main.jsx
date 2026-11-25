import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import ManagerSignIn from './ManagerSignIn.jsx'
import './styles.css'

createRoot(document.getElementById('root')).render(
  <StrictMode><ManagerSignIn /></StrictMode>,
)
