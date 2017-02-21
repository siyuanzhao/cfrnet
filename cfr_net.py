import tensorflow as tf
import numpy as np

SQRT_CONST = 1e-3

class cfr_net:
    """
    cfr_net implements the counterfactual regression neural network
    by F. Johansson, U. Shalit and D. Sontag: https://arxiv.org/abs/1606.03976

    This file contains the class cfr_net as well as helper functions.
    The network is implemented as a tensorflow graph. The class constructor
    creates an object containing relevant TF nodes as member variables.
    """

    def __init__(self, x, t, y_ , p, FLAGS, r_alpha, r_lambda, do_in, do_out, dims):
        """
        Constructs a TensorFlow subgraph for counterfactual regression.
        Sets the following member variables (to TF nodes):

        self.output         The output prediction "y"
        self.tot_loss       The total objective to minimize
        self.imb_loss       The imbalance term of the objective
        self.pred_loss      The prediction term of the objective
        self.weights_in     The input/representation layer weights
        self.weights_out    The output/post-representation layer weights
        self.weights_pred   The (linear) prediction layer weights
        self.h_rep          The layer of the penalized representation
        """

        self.x = x
        self.t = t
        self.y_ = y_
        self.p = p
        self.r_alpha = r_alpha
        self.r_lambda = r_lambda
        self.do_in = do_in
        self.do_out = do_out

        dim_input = dims[0]
        dim_in = dims[1]
        dim_out = dims[2]

        weights_in = []; biases_in = []
        weights_out = []; biases_out = []

        n_in = FLAGS.n_in
        n_out = FLAGS.n_out
        weight_init = FLAGS.weight_init
        sig = FLAGS.rbf_sigma

        if n_in == 0 or (n_in == 1 and FLAGS.varsel):
            dim_in = dim_input
        if n_out == 0:
            dim_out = dim_in+1

        if FLAGS.batch_norm:
            bn_biases = []
            bn_scales = []

        ''' Construct input/representation layers '''
        h_in = [x]
        for i in range(0,n_in):
            if i==0:
                ''' If using variable selection, first layer is just rescaling'''
                if FLAGS.varsel:
                    weights_in.append(tf.Variable(1.0/dim_input*tf.ones([dim_input])))
                else:
                    weights_in.append(tf.Variable(tf.random_normal([dim_input,dim_in], stddev=weight_init)))
            else:
                weights_in.append(tf.Variable(tf.random_normal([dim_in,dim_in], stddev=weight_init)))

            ''' If using variable selection, first layer is just rescaling'''
            if FLAGS.varsel and i==0:
                biases_in.append([])
                h_in.append(tf.mul(h_in[i],weights_in[i]))
            else:
                biases_in.append(tf.Variable(tf.random_normal([1,dim_in], stddev=weight_init)))
                z = tf.matmul(h_in[i], weights_in[i]) + biases_in[i]

                if FLAGS.batch_norm:
                    batch_mean, batch_var = tf.nn.moments(z, [0])
                    bn_biases.append(tf.Variable(tf.ones([dim_in])))
                    bn_scales.append(tf.Variable(tf.ones([dim_in])))
                    z = tf.nn.batch_normalization(z, batch_mean, batch_var, bn_biases[-1], bn_scales[-1], 1e-3)

                h_in.append(tf.nn.relu(z))
                h_in[i+1] = tf.nn.dropout(h_in[i+1], do_in)

        h_rep = h_in[len(h_in)-1]

        ''' Construct output/regression layers '''
        # residual block
        it = tf.where(t>0)[:,0]
        ic = tf.where(t<1)[:,0]

        #h_out = [tf.concat(1,[h_rep,t])]
        h_out = [h_rep]
        for i in range(0,n_out):
            if i==0:
                weights_out.append(tf.Variable(tf.random_normal([dim_in,dim_out], stddev=weight_init)))
            else:
                weights_out.append(tf.Variable(tf.random_normal([dim_out,dim_out], stddev=weight_init)))
            biases_out.append(tf.Variable(tf.random_normal([1,dim_out], stddev=weight_init)))
            z = tf.matmul(h_rep, weights_out[i]) + biases_out[i]

            if i == (n_out-1):
                h_out.append(tf.nn.relu(z+h_rep))
            else:
                h_out.append(tf.nn.relu(z))
            h_out[i+1] = tf.nn.dropout(h_out[i+1], do_out)

        weights_pred = tf.Variable(tf.random_normal([dim_out,1], stddev=weight_init))
        bias_pred = tf.Variable(tf.random_normal([1], stddev=weight_init))
        ''' Construct linear classifier '''
        hf_pred = h_out[len(h_out)-1]
        # prediction in treatment
        yt = tf.matmul(hf_pred, weights_pred)+bias_pred
        # prediction in control
        yc = tf.matmul(h_rep, weights_pred)+bias_pred

        y = t*yt + (1-t)*yc
        pred_yt = tf.gather(yt, it)
        pred_yc = tf.gather(yc, ic)
        yt_ = tf.gather(y_, it)
        yc_ = tf.gather(y_, ic)
        ''' Construct loss function '''
        #sq_error = tf.reduce_mean(tf.concat(0, [tf.square(yt_ - pred_yt), tf.square(yc_ - pred_yc)]))
        sq_error = tf.reduce_mean(tf.square(y_ - y))
        pred_error = tf.sqrt(sq_error)

        if FLAGS.loss == 'l1':
            risk = tf.reduce_mean(tf.concat(0, [tf.abs(yt_ - pred_yt), tf.abs(yc_ - pred_yc)]))
        elif FLAGS.loss == 'log':
            pred_yt = 0.995/(1.0+tf.exp(-pred_yt)) + 0.0025
            pred_yc = 0.995/(1.0+tf.exp(-pred_yc)) + 0.0025
            risk = -tf.reduce_mean(tf.concat(0, [yt_*tf.log(pred_yt) + (1.0-yt_)*tf.log(1.0-pred_yt),
                                                 yc_*tf.log(pred_yc) + (1.0-yc_)*tf.log(1.0-pred_yc)]))
            pred_error = risk
        else:
            risk = sq_error

        ''' Regularization '''
        if FLAGS.p_lambda>0:
            if FLAGS.varsel or n_out == 0:
                regularization = tf.nn.l2_loss(tf.slice(weights_pred,[0,0],[dim_out-1,1])) #don't penalize treatment coefficient
            else:
                regularization = tf.nn.l2_loss(weights_pred)

            for i in range(0,n_out):
                regularization = regularization + tf.nn.l2_loss(weights_out[i])

            for i in range(0,n_in):
                if not (FLAGS.varsel and i==0): # No penalty on W in variable selection
                    regularization = regularization + tf.nn.l2_loss(weights_in[i])

        '''Entropy minimization'''
        
        ''' Imbalance error '''
        if FLAGS.imb_fun == 'mmd2_rbf':
            imb_dist = mmd2_rbf(h_rep,t,p,sig)
            imb_error = r_alpha*imb_dist
        elif FLAGS.imb_fun == 'mmd2_lin':
            imb_dist = mmd2_lin(h_rep,t,p)
            imb_error = r_alpha*mmd2_lin(h_rep,t,p)
        elif FLAGS.imb_fun == 'mmd_rbf':
            imb_dist = tf.abs(mmd2_rbf(h_rep,t,p,sig))
            imb_error = tf.sqrt(SQRT_CONST + tf.square(r_alpha)*imb_dist)
        elif FLAGS.imb_fun == 'mmd_lin':
            imb_dist = mmd2_lin(h_rep,t,p)
            imb_error = tf.sqrt(SQRT_CONST + tf.square(r_alpha)*imb_dist)
        elif FLAGS.imb_fun == 'wass':
            imb_dist = wasserstein(h_rep,t,p,lam=FLAGS.wass_lambda,its=FLAGS.wass_iterations,sq=False,backpropT=FLAGS.wass_bpt)
            imb_error = r_alpha * imb_dist
        elif FLAGS.imb_fun == 'wass2':
            imb_dist = wasserstein(h_rep,t,p,lam=FLAGS.wass_lambda,its=FLAGS.wass_iterations,sq=True,backpropT=FLAGS.wass_bpt)
            imb_error = r_alpha * imb_dist
        else:
            imb_dist = lindisc(h_rep,p,t)
            imb_error = r_alpha * imb_dist

        ''' Total error '''
        tot_error = risk

        if FLAGS.p_alpha>0:
            tot_error = tot_error + imb_error

        if FLAGS.p_lambda>0:
            tot_error = tot_error + r_lambda*regularization

        self.output = y
        self.tot_loss = tot_error
        self.imb_loss = imb_error
        self.imb_dist = imb_dist
        self.pred_loss = pred_error
        self.weights_in = weights_in
        self.weights_out = weights_out
        self.weights_pred = weights_pred
        self.h_rep = h_rep

def lindisc(X,p,t):
    ''' Linear MMD '''

    it = tf.where(t>0)[:,0]
    ic = tf.where(t<1)[:,0]

    Xc = tf.gather(X,ic)
    Xt = tf.gather(X,it)

    mean_control = tf.reduce_mean(Xc,reduction_indices=0)
    mean_treated = tf.reduce_mean(Xt,reduction_indices=0)

    c = tf.square(2*p-1)*0.25
    f = tf.sign(p-0.5)

    mmd = tf.reduce_sum(tf.square(p*mean_treated - (1-p)*mean_control))
    mmd = f*(p-0.5) + tf.sqrt(c + mmd + SQRT_CONST)

    return mmd

def mmd2_lin(X,t,p):
    ''' Linear MMD '''

    it = tf.where(t>0)[:,0]
    ic = tf.where(t<1)[:,0]

    Xc = tf.gather(X,ic)
    Xt = tf.gather(X,it)

    mean_control = tf.reduce_mean(Xc,reduction_indices=0)
    mean_treated = tf.reduce_mean(Xt,reduction_indices=0)

    mmd = tf.reduce_sum(tf.square(2.0*p*mean_treated - 2.0*(1.0-p)*mean_control))

    return mmd

def mmd2_rbf(X,t,p,sig):
    """ Computes the l2-RBF MMD for X given t """

    it = tf.where(t>0)[:,0]
    ic = tf.where(t<1)[:,0]

    Xc = tf.gather(X,ic)
    Xt = tf.gather(X,it)

    Kcc = tf.exp(-pdist2sq(Xc,Xc)/tf.square(sig))
    Kct = tf.exp(-pdist2sq(Xc,Xt)/tf.square(sig))
    Ktt = tf.exp(-pdist2sq(Xt,Xt)/tf.square(sig))

    m = tf.to_float(tf.shape(Xc)[0])
    n = tf.to_float(tf.shape(Xt)[0])

    mmd = tf.square(1.0-p)/(m*(m-1.0))*(tf.reduce_sum(Kcc)-m)
    mmd = mmd + tf.square(p)/(n*(n-1.0))*(tf.reduce_sum(Ktt)-n)
    mmd = mmd - 2.0*p*(1.0-p)/(m*n)*tf.reduce_sum(Kct)
    mmd = 4.0*mmd

    return mmd

def pdist2sq(X,Y):
    """ Computes the squared Euclidean distance between all pairs x in X, y in Y """
    C = -2*tf.matmul(X,tf.transpose(Y))
    nx = tf.reduce_sum(tf.square(X),1,keep_dims=True)
    ny = tf.reduce_sum(tf.square(Y),1,keep_dims=True)
    D = (C + tf.transpose(ny)) + nx
    return D

def pdist2(X,Y):
    """ Returns the tensorflow pairwise distance matrix """
    return tf.sqrt(SQRT_CONST + pdist2sq(X,Y))

def pop_dist(X,t):
    it = tf.where(t>0)[:,0]
    ic = tf.where(t<1)[:,0]
    Xc = tf.gather(X,ic)
    Xt = tf.gather(X,it)
    nc = tf.to_float(tf.shape(Xc)[0])
    nt = tf.to_float(tf.shape(Xt)[0])

    ''' Compute distance matrix'''
    M = pdist2(Xt,Xc)
    return M

def wasserstein(X,t,p,lam=10,its=10,sq=False,backpropT=False):
    """ Returns the Wasserstein distance between treatment groups """
    it = tf.where(t>0)[:,0]
    ic = tf.where(t<1)[:,0]
    Xc = tf.gather(X,ic)
    Xt = tf.gather(X,it)
    nc = tf.to_float(tf.shape(Xc)[0])
    nt = tf.to_float(tf.shape(Xt)[0])

    ''' Compute distance matrix'''
    if sq:
        M = pdist2sq(Xt,Xc)
    else:
        M = tf.sqrt(1e-2 + pdist2sq(Xt,Xc))

    ''' Estimate lambda and delta '''
    M_mean = tf.reduce_mean(M)
    M_drop = tf.nn.dropout(M,10/(nc*nt))
    delta = tf.stop_gradient(tf.reduce_max(M))
    eff_lam = tf.stop_gradient(lam/M_mean)

    ''' Compute new distance matrix '''
    Mt = M
    row = delta*tf.ones(tf.shape(M[0:1,:]))
    col = tf.concat(0,[delta*tf.ones(tf.shape(M[:,0:1])),tf.zeros((1,1))])
    Mt = tf.concat(0,[M,row])
    Mt = tf.concat(1,[Mt,col])

    ''' Compute marginal vectors '''
    a = tf.concat(0,[p*tf.ones(tf.shape(tf.where(t>0)[:,0:1]))/nt, (1-p)*tf.ones((1,1))])
    b = tf.concat(0,[(1-p)*tf.ones(tf.shape(tf.where(t<1)[:,0:1]))/nc, p*tf.ones((1,1))])

    ''' Compute kernel matrix'''
    Mlam = eff_lam*Mt
    K = tf.exp(-Mlam)
    U = K*Mt
    ainvK = K/a

    u = a
    for i in range(0,its):
        u = 1.0/(tf.matmul(ainvK,(b/tf.transpose(tf.matmul(tf.transpose(u),K)))))
    v = b/(tf.transpose(tf.matmul(tf.transpose(u),K)))

    T = u*(tf.transpose(v)*K)

    if not backpropT:
        T = tf.stop_gradient(T)

    D = 2*tf.reduce_sum(T*Mt)

    return D

def simplex_project(x,k):
    """ Projects a vector x onto the k-simplex """
    d = x.shape[0]
    mu = np.sort(x,axis=0)[::-1]
    nu = (np.cumsum(mu)-k)/range(1,d+1)
    I = [i for i in range(0,d) if mu[i]>nu[i]]
    theta = nu[I[-1]]
    w = np.maximum(x-theta,0)
    return w
