import {UpdateReminder} from '../standard/ClientUpdate';
import type {ReactNode} from 'react';
export function DesktopSession({children}:{children:ReactNode}){return <>{children}</>}
export const DesktopActions=()=> <UpdateReminder/>;
export function DesktopSettings(_props:{section:string}){return null}
