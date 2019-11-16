#!/usr/bin/python3
# coding: UTF-8
"""
ライントレース Model In the Loop Simulation (MILS)
（α版）

説明

　コースデータは画像(PNGやJPG)として準備してください。
  プログラムと同じフォルダに置くかフォルダを指定してください。

　制御アルゴリズムの変更についてはLFController クラスの
　prs2mtrs() メソッドを編集してください。
　
　物理モデルの変更についてはLFPhysicalModel クラスの
  drive() メソッドを編集してください。

プロパティ

- コースデータ（モノクロ画像）
- サンプリングレート（秒）
- ライントレーサー
  - 制御：　フォトリフレクタ入力　-> [制御モジュール] -> モーター制御信号
  - 構成：　センサ位置（固定）、モータ特性（固定）
  - 状態：　座標、方向、速度、加速度
  - 振舞：　センシング、移動

機能

- コース表示
- ライントレーサー表示
- フォトリフレクタへの白黒情報を提供
- ライントレーサー位置情報の取得

準備 (Raspbian の場合)

 $ sudo apt-get install python3-numpy
 $ sudo apt-get install  python3-scipy
 $ sudo apt-get install python3-pygame
 $ sudo apt-get install python3-transitions

「電子情報通信設計製図」新潟大学工学部工学科電子情報通信プログラム

All rights revserved (c) Shogo MURAMATSU
"""
from transitions import Machine
from scipy.integrate import odeint
import numpy as np
import pygame
import sys
import math

# 色の定義
WHITE = (255, 255, 255)
BLACK = (  0,   0,   0)
GREEN = (  0, 255,   0)    
BLUE  = (  0,   0, 255)

# コースデータ画像
COURSE_IMG = 'lfcourse.png'

# フォトリフレクタ数
NUM_PHOTOREFS = 4

# メイン関数
def main():
    """ メイン関数 """

    # コースデータの読み込み
    course = LFCourse(COURSE_IMG)

    # シャフト中心(+)からのフォトリフレクタ(*)の
    # 相対座標[mm]
    #  
    #       --|--          * pr1 (dx1,dy1)
    #         |           * pr2 (dx2,dy2)       
    #  (0,0)  + -------------            → x
    #   ↓     |           * pr3 (dx3,dy3)       
    #   y   --|--          * pr4 (dx4,dy4)
    #
    # ((dx1,dy1), (dx2,dy2), (dx3,dy3), (dx4,dy4))
    mpp = ((120,-40), (110,-20), (110,20), (120,40))
    lf = LFPhysicalModel(course, weight = 200, mntposprs = mpp)

    # MILSオブジェクトのインスタンス生成
    mils = LFModelInTheLoopSimulation(lf)

    # シミュレーションの実行
    mils.run()

def clamped(v):
    """ 値の制限 [-1,1] """
    return max(-1,min(1,v))

def rotate_pos(pos,center,angle):
    """ 座標位置の回転 """
    rotmtx = np.asarray([
        [ np.cos(angle), -np.sin(angle) ],
        [ np.sin(angle),  np.cos(angle) ]
    ])
    return rotmtx.dot(pos-center) + center

class LFController:
    """ ライントレース制御クラス 
        
        ライントレース制御アルゴリズムを実装する。

        入力　フォトリフレクの値 [0,1]x4
        出力　モーター制御信号 [-1,1]x2
    """
    
    def __init__(self,prs = None):
        self._prs = prs

    def prs2mtrs(self):
        """ フォトリフレクタ→モータ制御 """

        # フォトリフレクタの値を読み出しとベクトル化(vec_x)
        # 白を検出すると 0，黒を検出すると 1
        vec_x = np.array([ self._prs[idx].value \
            for idx in range(NUM_PHOTOREFS) ])

        # モーター制御の強度値を計算（ここを工夫）
        mat_A = np.array([[-0.2, 0.0, 0.2, 0.4],\
            [0.4, 0.2, 0.0, -0.2]])
        vec_y = np.dot(mat_A,vec_x)

        # 出力範囲を[-1,1]に直して出力
        left, right = vec_y[0], vec_y[1]
        return (clamped(left),clamped(right))

    @property
    def photorefs(self):
        return self._prs

    @photorefs.setter
    def photorefs(self,prs):
        self._prs = prs

class LFPhotoReflector:
    """ フォトリフレクタクラス """

    def __init__(self,course,value = 0.0):
        self._course = course
        self._value = value
        self._pos_px = [0.0, 0.0]
    
    @property
    def value(self):
        return self.measurement()
    
    @value.setter
    def value(self,value):
        self._value = value

    @property
    def pos_px(self):
        return self._pos_px

    @pos_px.setter
    def pos_px(self,pos_px):
        self._pos_px = pos_px

    def measurement(self):
        # センサ位置周辺の値をリターン
        x_px = int(self._pos_px[0]+0.5)
        y_px = int(self._pos_px[1]+0.5)     
        if 1 < y_px and y_px < self._course.height-1 and \
            1 < x_px and x_px < self._course.width-1:
            pxarray = pygame.PixelArray(self._course.image) 
            # 3x3 領域の平均を出力
            acc = 0.0
            for row in range(-1,2):
                for col in range(-1,2):
                    acc = acc + float(pxarray[x_px+col][y_px+row] > 0)
            value = acc/9.0 # 平均値併産
        else:
            value = 0.5

        return value

class LFPhysicalModel:
    """ ライントレーサー物理モデルクラス 
        
        ライントレーサーの物理モデルを実装する。

        入力　モーター制御信号 [-1,1]x2        
        出力　フォトリフレクタの値 [0,1]x4 
    """    
    
    SHAFT_LENGTH = 50 # mm
    TIRE_DIAMETER = 20 # mm

    def __init__(self, \
        course, \
        weight = 200, \
        mntposprs =  ((120,-40), (110,-20), (110,20), (120,40)) \
        ):

        self._course = course
        self._weight = weight
        self._mntposprs = mntposprs
        self._x_mm = self.SHAFT_LENGTH + 10 # mm
        self._y_mm = self.SHAFT_LENGTH + 10 # mm
        self._angle = 0.0 # rad
        self.reset()

        # 制御機とフォトリフレクタ設定
        self._controller = LFController()
        self._prs = [ LFPhotoReflector(self._course) \
            for idx in range(NUM_PHOTOREFS)]
        for idx in range(NUM_PHOTOREFS):
            self._prs[idx].value = 0.0
        self._controller.photorefs = self._prs

    @property
    def course(self):
        return self._course
    
    @property
    def angle(self):
        return self._angle

    def reset(self):
        self._vx_mm = 0.0 # mm
        self._vy_mm = 0.0 # mm
        self._w = 0.0

    def set_position_mm(self,x,y):
        self._x_mm = x # mm
        self._y_mm = y # mm

    def move_px(self,dx_px,dy_px):
        res = self._course.resolution # mm/pixel        
        self._x_mm = self._x_mm + dx_px*res # mm
        self._y_mm = self._y_mm + dy_px*res # mm   

    def rotate(self,angle):
        self._angle = angle # rad

    def set_interval(self,interval):
        self._interval = interval

    def draw_body(self,screen):
        #
        rect = np.asarray(self.get_rect_px())
        center = np.asarray(self.get_center_px())
        #
        apos00 = np.dot([[1,0,0,0],[0,1,0,0]],rect)
        apos10 = np.dot([[1,0,0,0],[0,1,0,1]],rect)
        apos01 = np.dot([[1,0,1,0],[0,1,0,0]],rect)
        apos11 = np.dot([[1,0,1,0],[0,1,0,1]],rect)
        #
        angle = self._angle
        apos00 = rotate_pos(apos00,center,angle)
        apos10 = rotate_pos(apos10,center,angle)
        apos01 = rotate_pos(apos01,center,angle)
        apos11 = rotate_pos(apos11,center,angle)
        #
        pos00 = apos00.tolist()
        pos10 = apos10.tolist()
        pos01 = apos01.tolist()
        pos11 = apos11.tolist()
        #
        pygame.draw.polygon(screen, BLUE, [pos00,pos10,pos01,pos11],0)

        # TODO: 車体描画

        # フォトリフレクタ描画
        res = self._course.resolution # mm/pixel
        for idx in range(NUM_PHOTOREFS):
            pos = center + np.asarray(self._mntposprs[idx])/res
            pos = (rotate_pos(pos,center,angle)+.5).astype(np.int32).tolist()
            red = (int(self._prs[idx].value*255.0), 0, 0)
            pygame.draw.circle(screen, red, pos, 4)

    def get_rect_px(self):
        res = self._course.resolution # mm/pixel
        pos_cx_mm = self._x_mm # pixel
        pos_cy_mm = self._y_mm # pixel
        car_width_mm = 2*self.SHAFT_LENGTH # 車体幅 in mm
        car_length_mm = car_width_mm+60 # 車体長 in mm
        pos_topleft_x_px = int(pos_cx_mm/res-self.SHAFT_LENGTH+.5)+10
        pos_topleft_y_px = int(pos_cy_mm/res-self.SHAFT_LENGTH+.5)+10
        car_width_px = car_width_mm/res # 車体幅 in pixel  
        car_length_px = car_length_mm/res  # 車体長 in mm        
        rect = [pos_topleft_x_px,pos_topleft_y_px, car_length_px, car_width_px]
        return rect

    def get_center_px(self):
        res = self._course.resolution # mm/pixel        
        cx_mm = self._x_mm # pixel
        cy_mm = self._y_mm # pixel
        cx_px = cx_mm/res
        cy_px = cy_mm/res
        return [cx_px,cy_px]

    def drive(self,fps):
        # モデルパラメータ
        sFwd = 1.0
        sRot = 3.0
        kFwd = 3.0 # 抵抗係数
        kRot = 1.0 # 抵抗係数
        weight_kg = 1e-3*self._weight
        # センサ値更新 
        self._sense()
        # モーター制御信号取得
        mtrs = np.asarray(self._controller.prs2mtrs())
        # 力の計算
        forceFwd = mtrs[0]+mtrs[1]
        forceRot = mtrs[0]-mtrs[1]    
        # 加速度の計算
        angle = self._angle # 車体の方向
        direction  = np.asarray([ np.cos(angle), np.sin(angle)]) 
        accelFwd = sFwd*forceFwd*direction/weight_kg
        accelRot = sRot*forceRot/weight_kg
        # 運動方程式
        #
        # d^2p/dt^2 = -k/m dp/dt + a(t)
        #
        # d_ (  p ) =  ( 0     1  )( p ) + ( 0 )
        # dt (  p')    ( 0   -k/m )( p')   ( a )
        #
        # p = ( x )
        #     ( y )
        pos = [ 0.0 for idx in range(4) ]
        pos[0] = 1e-3*self._x_mm # m
        pos[1] = 1e-3*self._y_mm # m
        pos[2] = 1e-3*self._vx_mm # m
        pos[3] = 1e-3*self._vy_mm # m
        t = np.linspace(0,1/fps,2)
        y = odeint(self._f,pos,t,args=(-kFwd/weight_kg,accelFwd))
        pos = y[-1]
        self._x_mm  = 1e3*pos[0]
        self._y_mm  = 1e3*pos[1]
        self._vx_mm = 1e3*pos[2]
        self._vy_mm = 1e3*pos[3]
        #
        ang = [0.0, 0.0]
        ang[0] = self._angle
        ang[1] = self._w
        z = odeint(self._g,ang,t,args=(-kRot/weight_kg,accelRot))
        ang = z[-1]
        self._angle = ang[0]
        self._w     = ang[1]        

    def _f(self,p,t,c,a):
        return [ p[2], p[3], c*p[2]+a[0], c*p[3]+a[1] ]

    def _g(self,w,t,c,a):
        return [ w[1], c*w[1]+a ]

    def _sense(self):
        # 解像度
        res = self._course.resolution # mm/pixel                
        # 車体の位置と向き
        center_px = self.get_center_px()
        angle = self._angle

        # フォトリフレクタ位置を設定
        for idx in range(NUM_PHOTOREFS):
            pos = center_px + np.asarray(self._mntposprs[idx])/res
            self._prs[idx].pos_px = rotate_pos(pos,center_px,angle)        

class LFCourse:
    """ コースデータ 
    
        ライントレース用のコースデータを保持する。

        res [mm/pixel]

    """
    def __init__(self,filename,res=1.25):
        self._filename = filename
        self._width  = 640
        self._height = 360
        self._res = res
        image = pygame.image.load(self._filename)
        self._image = pygame.transform.scale(image,(640,360))

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    @property
    def resolution(self):
        return self._res

    @property
    def realwidth(self):
        return self._width*self._res

    @property
    def realheight(self):
        return self._height*self._res

    @property
    def image(self):
        return self._image

class LFModelInTheLoopSimulation(object):
    """ ライントレースMILSクラス """

    # シミュレータ状態の定義
    STATES = ('sinit','slocate','srotate','swait','srun','squit')

    # シミュレータ遷移の定義
    #
    #               +-------------------------------+
    #               ↓                           　  ↑
    # (sinit) → (slocate) → (srotate) → (swait) → (srun)
    #               |           |          |        |
    #               +-----------+-----+----+--------+
    #                                 ↓
    #                              (squit) 
    #
    # sinit:   初期設定*
    # slocate: ライントレーサー位置設定
    # srotate: ライントレーサー角度設定
    # swait:   走行準備
    # srun:    走行
    # squit:   終了
    #
    TRANSITIONS = (
        {'trigger': 'initialized', 'source': 'sinit',   'dest': 'slocate' },
        {'trigger': 'located',     'source': 'slocate', 'dest': 'srotate', 'after': 'lflag_false' },        
        {'trigger': 'rotated',     'source': 'srotate', 'dest': 'swait',   'after': 'rflag_false' },
        {'trigger': 'start',       'source': 'swait',   'dest': 'srun' },
        {'trigger': 'stop',        'source': 'srun',    'dest': 'slocate', 'after': 'reset' },                
        {'trigger': 'quit',        'source': 'slocate', 'dest': 'squit',   'after': 'close'  },
        {'trigger': 'quit',        'source': 'srotate', 'dest': 'squit',   'after': 'close'  },
        {'trigger': 'quit',        'source': 'swait',   'dest': 'squit',   'after': 'close'  },
        {'trigger': 'quit',        'source': 'srun',    'dest': 'squit',   'after': 'close'  }
    )

    def __init__(self, linefollower, fps = 10):

        self._clock = pygame.time.Clock()
        self._fps = fps

        # スクリーン設定
        self._linefollower = linefollower
        self._course = self._linefollower.course
        self._width  = self._course.width
        self._height = self._course.height
        self._screen = pygame.display.set_mode((self._width,self._height))

        # 状態遷移機械(SFM)の設定
        self._sfm = Machine(\
            model=self, \
            states=self.STATES, \
            initial=self.STATES[0], \
            transitions=self.TRANSITIONS, \
            ignore_invalid_triggers=True
        )
        self._flag_drag = False
        self._flag_rot  = False

    def run(self):

        pygame.init()
        pygame.display.set_caption('ライントレース・シミュレーター')
        font40 = pygame.font.Font(None, 40)
        font20 = pygame.font.Font(None, 20)        

        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.quit()

            # 背景描画
            self._screen.blit(self._course.image,[0, 0])

            # 位置設定
            mouseX, mouseY = pygame.mouse.get_pos()
            mBtn1, mBtn2, mBtn3 = pygame.mouse.get_pressed()
            #txt1 = '{},{}'.format(mouseX, mouseY)
            #txt2 = '{}:{}:{}'.format(mBtn1,mBtn2,mBtn3)

            key = pygame.key.get_pressed()
            if self.state == 'sinit': 
                # 初期化設定
                self._linefollower.set_position_mm(60,60) # mm
                self.initialized()

            msg = font20.render('', True, BLUE) 
            if self.state == 'slocate': 
                # マウスポインタが車体上かつ左クリックならば
                # 車体をドラッグ
                rect = self._linefollower.get_rect_px()
                isMouseIn = rect[0] < mouseX and mouseX < rect[0]+rect[2] and \
                    rect[1] < mouseY and mouseY < rect[1]+rect[3]
                if (self._flag_drag or isMouseIn) and mBtn1 == 1:
                    if not self._flag_drag:
                        preX, preY = mouseX, mouseY
                        self._flag_drag = True
                    dx_px, dy_px = mouseX - preX, mouseY - preY
                    self._linefollower.move_px(dx_px,dy_px)                    
                    preX, preY = mouseX, mouseY
                elif self._flag_drag: # 設定終了判定
                    self.located()

            if self.state == 'srotate': 
                # 左クリックならば車体を回転
                if mBtn1 == 1:
                    if not self._flag_rot:
                        center_px = self._linefollower.get_center_px()
                        self._flag_rot = True
                    dx_px = mouseX - center_px[0]
                    dy_px = mouseY - center_px[1]
                    angle = math.atan2(dy_px,dx_px)
                    self._linefollower.rotate(angle)
                elif self._flag_rot and mBtn1 == 0: # 設定終了判定
                    self.rotated()

            if self.state == 'swait':
                if mBtn1 == 1:
                    if not self._flag_drag:
                        self._flag_drag = True
                elif self._flag_drag:
                    self._flag_drag = False
                    self.start()

            if self.state == 'srun':
                if mBtn1 == 1:
                    self.stop()
                else:
                    self._linefollower.drive(self._fps)

            # キーボード入力
            if key[pygame.K_ESCAPE] == 1: # [ESP] ストップ
                self.stop()
            if key[pygame.K_SPACE] == 1: # [SPACE] スタート
                self.start()                                                                
            if key[pygame.K_q] == 1: # [q] 終了
                self.quit()                                                                                                

            # 画面描画
            self._linefollower.draw_body(self._screen)
            sur = font40.render(self.state, True, BLACK)
            self._screen.blit(sur,[int(self._width/2.0),int(self._height/2.0)])
            self._screen.blit(msg,[10,self._height-20])          
            pygame.display.update()

            # クロック
            self._clock.tick(self._fps)

    def lflag_false(self):
        self._flag_drag = False

    def rflag_false(self):
        self._flag_rot = False    

    def reset(self):
        self._linefollower.reset()

    def close(self):
        pygame.quit()
        sys.exit()

if __name__ == '__main__':
    main()